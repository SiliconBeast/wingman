/*
 * Onboard ICM-40627 -> driver context for the supervisor.
 *
 * 100 Hz, bias removed at boot (board must be still for ~0.5 s), low-passed.
 *   braking  : longitudinal < -300 mg for 200 ms  -> WARNING downgraded to CAUTION
 *   swerve   : |lateral| > 300 mg for 100 ms during CAUTION/WARNING -> near-miss log
 *   impact   : |horizontal| > 2 g on one raw sample -> latch + freeze the black box
 *
 * Axis mapping assumes board +X = vehicle forward, +Y = right. Change AX_/AY_
 * below if the board is mounted differently.
 */
#include <math.h>

#include <zephyr/drivers/sensor.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "wm_app.h"

LOG_MODULE_REGISTER(wm_imu, LOG_LEVEL_INF);

#define AX_IDX  0
#define AX_SIGN 1
#define AY_IDX  1
#define AY_SIGN 1

#define IMU_PERIOD_MS    10
#define ZERO_SAMPLES     50
#define LPF_ALPHA        0.2
#define BRAKE_MG         (-300)
#define ACCEL_MG         200
#define SWERVE_MG        300
#define IMPACT_MG        2000.0
#define BRAKE_SAMPLES    20 /* 200 ms */
#define ACCEL_SAMPLES    20
#define SWERVE_SAMPLES   10 /* 100 ms */

static const struct device *const imu = DEVICE_DT_GET_OR_NULL(DT_ALIAS(accel0));

struct wm_imu_state g_imu;

static volatile bool rezero_requested = true;

void wm_imu_rezero(void)
{
	rezero_requested = true;
}

static void imu_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	if (!imu || !device_is_ready(imu)) {
		LOG_WRN("IMU not ready; running without driver context");
		return;
	}

	double bias[3] = {0}, acc[3] = {0};
	int n_zero = 0;
	double fx = 0, fy = 0;
	int brake_n = 0, accel_n = 0, swerve_n = 0;
	bool near_miss_logged = false;

	for (;;) {
		k_sleep(K_MSEC(IMU_PERIOD_MS));

		struct sensor_value v[3];

		if (sensor_sample_fetch(imu) != 0 ||
		    sensor_channel_get(imu, SENSOR_CHAN_ACCEL_XYZ, v) != 0) {
			continue;
		}
		double mg[3];

		for (int i = 0; i < 3; i++) {
			mg[i] = sensor_value_to_double(&v[i]) * 1000.0 / SENSOR_G * 1000000.0;
		}

		if (rezero_requested) {
			rezero_requested = false;
			n_zero = 0;
			acc[0] = acc[1] = acc[2] = 0;
		}
		if (n_zero < ZERO_SAMPLES) {
			for (int i = 0; i < 3; i++) {
				acc[i] += mg[i];
			}
			if (++n_zero == ZERO_SAMPLES) {
				for (int i = 0; i < 3; i++) {
					bias[i] = acc[i] / ZERO_SAMPLES;
				}
				fx = fy = 0;
				LOG_INF("IMU zeroed: bias %d %d %d mg", (int)bias[0], (int)bias[1],
					(int)bias[2]);
			}
			continue;
		}

		double x = AX_SIGN * (mg[AX_IDX] - bias[AX_IDX]);
		double y = AY_SIGN * (mg[AY_IDX] - bias[AY_IDX]);

		fx += LPF_ALPHA * (x - fx);
		fy += LPF_ALPHA * (y - fy);

		brake_n = fx < BRAKE_MG ? brake_n + 1 : 0;
		accel_n = fx > ACCEL_MG ? accel_n + 1 : 0;
		swerve_n = fabs(fy) > SWERVE_MG ? swerve_n + 1 : 0;

		bool braking = brake_n >= BRAKE_SAMPLES;
		bool swerving = swerve_n >= SWERVE_SAMPLES;
		uint8_t man = WM_MAN_STEADY;

		if (swerving) {
			man = WM_MAN_SWERVE;
		} else if (braking) {
			man = WM_MAN_BRAKE;
		} else if (accel_n >= ACCEL_SAMPLES) {
			man = WM_MAN_ACCEL;
		}

		bool impact = sqrt(x * x + y * y) > IMPACT_MG;

		k_spinlock_key_t key = k_spin_lock(&g_lock);
		bool first_impact = impact && !g_imu.impact_latched;
		enum wm_state st = g_sup.state;
		uint16_t threat_id = g_sup.threat.id;

		g_imu.ok = true;
		g_imu.ax_mg = (int16_t)CLAMP(fx, INT16_MIN, INT16_MAX);
		g_imu.ay_mg = (int16_t)CLAMP(fy, INT16_MIN, INT16_MAX);
		g_imu.maneuver = man;
		if (impact) {
			g_imu.impact_latched = true;
		}
		wm_sup_set_braking(&g_sup, braking);
		k_spin_unlock(&g_lock, key);

		if (first_impact) {
			wm_blackbox_freeze();
			LOG_WRN("IMPACT %d mg: black box frozen (wm blackbox)", (int)sqrt(x * x + y * y));
		}
		if (swerving && (st == WM_CAUTION || st == WM_WARNING)) {
			if (!near_miss_logged) {
				near_miss_logged = true;
				LOG_WRN("NEAR-MISS: swerve %d mg during %s, threat id=%u", (int)fy,
					wm_state_name(st), threat_id);
			}
		} else if (!swerving) {
			near_miss_logged = false;
		}
	}
}

K_THREAD_DEFINE(imu_tid, 2048, imu_fn, NULL, NULL, NULL, 6, 0, 500);
