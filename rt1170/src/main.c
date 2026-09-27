/*
 * Wingman RT1170 safety supervisor.
 *
 *   net_rx  (prio 3)  recvfrom :5005 -> stamp arrival -> parse/CRC -> ev_q
 *   decide  (prio 2)  ev_q -> wm_sup state machine -> LEDs/probes -> stats -> status_q
 *                     feeds the hardware watchdog every loop
 *   hb timer (10 ms)  no valid frame for heartbeat_timeout_ms -> EV_HB_LOST
 *   status_tx(prio 5) status_q -> sendto <last sender>:5006
 *   led timer (62 ms) blink patterns
 *   imu     (prio 6)  see imu.c
 *
 * The decide thread is the only writer of g_sup's state; the shell and IMU
 * threads touch it under g_lock.
 */
#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/watchdog.h>
#include <zephyr/logging/log.h>
#include <zephyr/net/socket.h>
#include <zephyr/sys/atomic.h>

#if defined(CONFIG_SOC_SERIES_IMXRT11XX)
#include <fsl_soc_src.h>
#endif

#include "wingman_proto.h"
#include "wm_app.h"

LOG_MODULE_REGISTER(wingman, LOG_LEVEL_INF);

#define PRIO_DECIDE    2
#define PRIO_NET_RX    3
#define PRIO_STATUS_TX 5

/* i.MX WDOG1 counts in 0.5 s steps; 500 ms is the shortest timeout it supports. */
#define WM_WDT_TIMEOUT_MS   500
#define WM_DECIDE_POLL_MS   20
#define WM_KEEPALIVE_MS     100
#define WM_DEFAULT_PEER_IP  "192.168.10.1"

struct k_spinlock g_lock;
struct wm_sup g_sup;
struct wm_measurements g_meas;
volatile bool g_hang_requested;

enum ev_kind { EV_FRAME, EV_BAD, EV_HB_LOST };

struct wm_event {
	uint8_t kind;
	uint8_t bad; /* enum wm_parse_result for EV_BAD */
	uint32_t t_arrival_cyc;
	wm_perception_t frame;
};

K_MSGQ_DEFINE(ev_q, sizeof(struct wm_event), 16, 4);
K_MSGQ_DEFINE(status_q, sizeof(wm_status_t), 8, 4);

/* ---------------------------------------------------------------- outputs */

static const struct gpio_dt_spec led_red = GPIO_DT_SPEC_GET_OR(DT_ALIAS(led3), gpios, {0});
static const struct gpio_dt_spec led_green = GPIO_DT_SPEC_GET_OR(DT_ALIAS(led4), gpios, {0});
static const struct gpio_dt_spec som_red = GPIO_DT_SPEC_GET_OR(DT_ALIAS(led1), gpios, {0});
static const struct gpio_dt_spec som_green = GPIO_DT_SPEC_GET_OR(DT_ALIAS(led0), gpios, {0});

/* Logic-analyzer probes on the 60-pin expansion header, defined in app.overlay. */
#define ZEPHYR_USER DT_PATH(zephyr_user)
static const struct gpio_dt_spec probe_rx = GPIO_DT_SPEC_GET_OR(ZEPHYR_USER, rx_probe_gpios, {0});
static const struct gpio_dt_spec probe_alert =
	GPIO_DT_SPEC_GET_OR(ZEPHYR_USER, alert_probe_gpios, {0});

static const struct gpio_dt_spec *const all_pins[] = {&led_red, &led_green, &som_red,
						      &som_green, &probe_rx, &probe_alert};

static void pin_set(const struct gpio_dt_spec *p, int v)
{
	if (p->port) {
		gpio_pin_set_dt(p, v);
	}
}

static void outputs_init(void)
{
	for (size_t i = 0; i < ARRAY_SIZE(all_pins); i++) {
		const struct gpio_dt_spec *p = all_pins[i];

		if (!p->port) {
			continue;
		}
		if (!gpio_is_ready_dt(p) || gpio_pin_configure_dt(p, GPIO_OUTPUT_INACTIVE) != 0) {
			LOG_WRN("output pin %u on %s unavailable", p->pin, p->port->name);
		}
	}
}

static atomic_t led_state = ATOMIC_INIT(WM_BOOT);
static atomic_t led_tick;

/* tick advances every 62.5 ms: bit0 -> 8 Hz blink, bit3 -> 1 Hz blink */
static void leds_write(enum wm_state st, uint32_t tick)
{
	int fast = !(tick & 1u);
	int slow = !(tick & 8u);
	int r = 0, g = 0;

	switch (st) {
	case WM_WAIT_LINK:
		g = slow;
		break;
	case WM_NOMINAL:
		g = 1;
		break;
	case WM_CAUTION: /* amber = red + green */
		r = 1;
		g = 1;
		break;
	case WM_WARNING:
		r = fast;
		break;
	case WM_FAILSAFE:
		r = 1;
		break;
	default:
		break;
	}
	pin_set(&led_red, r);
	pin_set(&som_red, r);
	pin_set(&led_green, g);
	pin_set(&som_green, g);
}

static void led_timer_fn(struct k_timer *t)
{
	ARG_UNUSED(t);
	leds_write((enum wm_state)atomic_get(&led_state), (uint32_t)atomic_inc(&led_tick) + 1);
}
K_TIMER_DEFINE(led_timer, led_timer_fn, NULL);

/* Called by decide on every accepted decision. Writing the LEDs here (not only
 * from the blink timer) makes "outputs written" a real GPIO write.
 */
static void outputs_apply(enum wm_state st, bool changed)
{
	if (changed) {
		atomic_set(&led_state, st);
		atomic_set(&led_tick, 0);
		leds_write(st, 0);
	}
	if (probe_alert.port) {
		gpio_pin_toggle_dt(&probe_alert);
	}
}

/* ---------------------------------------------------------------- watchdog */

static const struct device *const wdt = DEVICE_DT_GET_OR_NULL(DT_ALIAS(watchdog0));
static int wdt_chan = -1;

static void watchdog_start(void)
{
	if (!wdt || !device_is_ready(wdt)) {
		LOG_ERR("watchdog not available");
		return;
	}
	struct wdt_timeout_cfg cfg = {
		.window = {.min = 0, .max = WM_WDT_TIMEOUT_MS},
		.callback = NULL,
		.flags = WDT_FLAG_RESET_SOC,
	};

	wdt_chan = wdt_install_timeout(wdt, &cfg);
	if (wdt_chan < 0) {
		LOG_ERR("wdt_install_timeout: %d", wdt_chan);
		return;
	}
	int err = wdt_setup(wdt, WDT_OPT_PAUSE_HALTED_BY_DBG);

	if (err) {
		LOG_ERR("wdt_setup: %d", err);
		wdt_chan = -1;
		return;
	}
	LOG_INF("watchdog armed, %d ms", WM_WDT_TIMEOUT_MS);
}

static inline void watchdog_feed(void)
{
	if (wdt_chan >= 0) {
		wdt_feed(wdt, wdt_chan);
	}
}

static bool last_reset_was_watchdog(void)
{
#if defined(CONFIG_SOC_SERIES_IMXRT11XX)
	uint32_t f = SRC_GetResetStatusFlags(SRC);
	const uint32_t wdog = kSRC_M7CoreWdogResetFlag | kSRC_M7CoreWdog3ResetFlag |
			      kSRC_M7CoreWdog4ResetFlag;

	LOG_INF("SRC SRSR = 0x%08x", f);
	SRC_ClearGlobalSystemResetStatus(SRC, f);
	return (f & wdog) != 0;
#else
	return false;
#endif
}

/* ---------------------------------------------------------------- heartbeat */

static atomic_t hb_armed;
static atomic_t last_valid_ms;

static void hb_timer_fn(struct k_timer *t)
{
	ARG_UNUSED(t);
	if (!atomic_get(&hb_armed)) {
		return;
	}
	uint32_t age = k_uptime_get_32() - (uint32_t)atomic_get(&last_valid_ms);

	if (age > g_sup.th.heartbeat_timeout_ms) {
		struct wm_event ev = {.kind = EV_HB_LOST};

		/* Only disarm once the event is queued, so a full queue retries next tick. */
		if (k_msgq_put(&ev_q, &ev, K_NO_WAIT) == 0) {
			atomic_clear(&hb_armed);
		}
	}
}
K_TIMER_DEFINE(hb_timer, hb_timer_fn, NULL);

/* ---------------------------------------------------------------- network */

static struct k_spinlock peer_lock;
static struct sockaddr_in peer;

static void net_rx_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	static uint8_t buf[256];
	static struct wm_event ev;
	int s = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);

	if (s < 0) {
		LOG_ERR("socket: %d", errno);
		return;
	}
	struct sockaddr_in local = {
		.sin_family = AF_INET,
		.sin_port = htons(WM_PORT_PERCEPTION),
		.sin_addr = {.s_addr = htonl(INADDR_ANY)},
	};

	if (zsock_bind(s, (struct sockaddr *)&local, sizeof(local)) < 0) {
		LOG_ERR("bind :%d: %d", WM_PORT_PERCEPTION, errno);
		return;
	}
	LOG_INF("listening on udp :%d", WM_PORT_PERCEPTION);

	for (;;) {
		struct sockaddr_in src;
		socklen_t slen = sizeof(src);
		ssize_t n = zsock_recvfrom(s, buf, sizeof(buf), 0, (struct sockaddr *)&src, &slen);
		uint32_t t = k_cycle_get_32(); /* first thing */

		if (n < 0) {
			continue;
		}
		pin_set(&probe_rx, 1);

		enum wm_parse_result r = wm_parse_perception(buf, (size_t)n, &ev.frame);

		ev.t_arrival_cyc = t;
		if (r == WM_PARSE_OK) {
			ev.kind = EV_FRAME;
			k_spinlock_key_t key = k_spin_lock(&peer_lock);

			peer = src;
			peer.sin_port = htons(WM_PORT_STATUS);
			k_spin_unlock(&peer_lock, key);
		} else {
			ev.kind = EV_BAD;
			ev.bad = (uint8_t)r;
		}
		if (k_msgq_put(&ev_q, &ev, K_NO_WAIT) != 0) {
			k_spinlock_key_t key = k_spin_lock(&g_lock);

			g_meas.queue_overflow++;
			k_spin_unlock(&g_lock, key);
			pin_set(&probe_rx, 0);
		}
	}
}

static void status_tx_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	int s = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);

	if (s < 0) {
		LOG_ERR("status socket: %d", errno);
		return;
	}
	for (;;) {
		wm_status_t st;
		struct sockaddr_in dst;

		k_msgq_get(&status_q, &st, K_FOREVER);
		k_spinlock_key_t key = k_spin_lock(&peer_lock);

		dst = peer;
		k_spin_unlock(&peer_lock, key);

		if (zsock_sendto(s, &st, sizeof(st), 0, (struct sockaddr *)&dst, sizeof(dst)) < 0) {
			key = k_spin_lock(&g_lock);
			g_meas.status_tx_fail++;
			k_spin_unlock(&g_lock, key);
		}
	}
}

/* ---------------------------------------------------------------- black box */

static struct wm_bb_entry bb_ring[WM_BB_LEN];
static struct wm_bb_entry bb_frozen[WM_BB_LEN];
static uint32_t bb_head;
static bool bb_has_frozen;

/* caller holds g_lock */
static void blackbox_record(const struct wm_bb_entry *e)
{
	bb_ring[bb_head % WM_BB_LEN] = *e;
	bb_head++;
}

void wm_blackbox_freeze(void)
{
	k_spinlock_key_t key = k_spin_lock(&g_lock);

	for (uint32_t i = 0; i < WM_BB_LEN; i++) {
		bb_frozen[i] = bb_ring[(bb_head + i) % WM_BB_LEN]; /* oldest first */
	}
	bb_has_frozen = true;
	k_spin_unlock(&g_lock, key);
}

void wm_blackbox_print(void (*print)(void *ctx, const struct wm_bb_entry *e), void *ctx)
{
	if (!bb_has_frozen) {
		return;
	}
	for (uint32_t i = 0; i < WM_BB_LEN; i++) {
		if (bb_frozen[i].t_ms != 0) {
			print(ctx, &bb_frozen[i]);
		}
	}
}

/* ---------------------------------------------------------------- decide */

void wm_meas_reset(void)
{
	wm_hist_reset(&g_meas.latency);
	wm_run_reset(&g_meas.interval_us);
	wm_run_reset(&g_meas.failover_ms);
	g_meas.queue_overflow = 0;
	g_meas.status_tx_fail = 0;
}

static void queue_status(uint32_t seq, uint64_t tx_time_us, uint32_t latency_ns)
{
	wm_status_t st = {
		.magic = WM_MAGIC_STATUS,
		.version = WM_VERSION,
		.seq_echo = seq,
		.tx_time_us_echo = tx_time_us,
		.decision_latency_ns = latency_ns,
	};
	k_spinlock_key_t key = k_spin_lock(&g_lock);

	st.state = (uint8_t)g_sup.state;
	st.ttc_ms = g_sup.threat.ttc_ms;
	st.threat_id = g_sup.threat.id;
	st.fault_flags = wm_sup_take_faults(&g_sup);
	st.imu_ax_mg = g_imu.ax_mg;
	st.imu_ay_mg = g_imu.ay_mg;
	st.maneuver = g_imu.maneuver;
	k_spin_unlock(&g_lock, key);

	st.crc16 = wm_crc16(&st, WM_STATUS_CRC_LEN);
	k_msgq_put(&status_q, &st, K_NO_WAIT); /* drop if the sender is behind */
}

static void log_transition(enum wm_state from, enum wm_state to, const struct wm_threat *t)
{
	if (t->id != WM_THREAT_NONE) {
		LOG_INF("[%u] %s -> %s id=%u ttc=%dms dist=%d.%dm", k_uptime_get_32(),
			wm_state_name(from), wm_state_name(to), t->id, t->ttc_ms,
			t->dist_mm / 1000, (t->dist_mm % 1000) / 100);
	} else {
		LOG_INF("[%u] %s -> %s", k_uptime_get_32(), wm_state_name(from), wm_state_name(to));
	}
}

static void decide_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	static struct wm_event ev;
	uint32_t last_accept_cyc = 0;
	bool have_last_accept = false;
	uint32_t last_seq = 0;
	uint64_t last_tx_time = 0;
	int64_t last_keepalive = 0;

	watchdog_start();

	for (;;) {
		watchdog_feed();

		if (g_hang_requested) {
			printk("wm: decide thread stalled on purpose; watchdog reset in <= %d ms\n",
			       WM_WDT_TIMEOUT_MS);
			for (;;) {
				k_busy_wait(1000);
			}
		}

		if (k_msgq_get(&ev_q, &ev, K_MSEC(WM_DECIDE_POLL_MS)) != 0) {
			/* Idle: while failed safe, keep telling the TX2 so it can show it. */
			if (g_sup.state == WM_FAILSAFE &&
			    k_uptime_get() - last_keepalive >= WM_KEEPALIVE_MS) {
				last_keepalive = k_uptime_get();
				queue_status(last_seq, last_tx_time, 0);
			}
			continue;
		}

		k_spinlock_key_t key = k_spin_lock(&g_lock);
		enum wm_state before = g_sup.state;
		bool accepted = false;

		switch (ev.kind) {
		case EV_FRAME:
			accepted = wm_sup_on_frame(&g_sup, &ev.frame);
			break;
		case EV_BAD:
			wm_sup_on_bad(&g_sup, (enum wm_parse_result)ev.bad);
			break;
		case EV_HB_LOST:
			wm_sup_on_heartbeat_lost(&g_sup);
			break;
		}
		enum wm_state after = g_sup.state;
		struct wm_threat threat = g_sup.threat;

		k_spin_unlock(&g_lock, key);

		bool changed = before != after;

		if (ev.kind == EV_FRAME && accepted) {
			outputs_apply(after, changed);
			uint32_t t_out = k_cycle_get_32();

			pin_set(&probe_rx, 0);

			uint32_t lat_ns = (uint32_t)k_cyc_to_ns_floor64(t_out - ev.t_arrival_cyc);

			/* Heartbeat restarts from this frame. */
			atomic_set(&last_valid_ms, (atomic_val_t)k_uptime_get_32());
			atomic_set(&hb_armed, 1);

			key = k_spin_lock(&g_lock);
			wm_hist_add(&g_meas.latency, lat_ns);
			if (have_last_accept) {
				wm_run_add(&g_meas.interval_us,
					   k_cyc_to_ns_floor64(ev.t_arrival_cyc - last_accept_cyc) /
						   1000.0);
			}
			struct wm_bb_entry e = {
				.t_ms = k_uptime_get_32(),
				.state = (uint8_t)after,
				.threat_id = threat.id,
				.ttc_ms = threat.ttc_ms,
				.ax_mg = g_imu.ax_mg,
				.ay_mg = g_imu.ay_mg,
			};
			blackbox_record(&e);
			k_spin_unlock(&g_lock, key);

			last_accept_cyc = ev.t_arrival_cyc;
			have_last_accept = true;
			last_seq = ev.frame.seq;
			last_tx_time = ev.frame.tx_time_us;
			queue_status(last_seq, last_tx_time, lat_ns);
		} else {
			pin_set(&probe_rx, 0);
			if (changed) {
				outputs_apply(after, true);
			}
			if (ev.kind == EV_HB_LOST && changed && have_last_accept) {
				uint32_t t_out = k_cycle_get_32();

				key = k_spin_lock(&g_lock);
				wm_run_add(&g_meas.failover_ms,
					   k_cyc_to_ns_floor64(t_out - last_accept_cyc) / 1e6);
				k_spin_unlock(&g_lock, key);
			}
			if (ev.kind == EV_HB_LOST) {
				/* Intervals across an outage aren't jitter; and k_cycle wraps in ~4.3 s. */
				have_last_accept = false;
			}
			if (ev.kind != EV_FRAME) {
				last_keepalive = k_uptime_get();
				queue_status(last_seq, last_tx_time, 0);
			}
		}

		if (changed) {
			log_transition(before, after, &threat);
		}
	}
}

/* ---------------------------------------------------------------- main */

K_THREAD_STACK_DEFINE(decide_stack, 4096);
K_THREAD_STACK_DEFINE(net_rx_stack, 4096);
K_THREAD_STACK_DEFINE(status_tx_stack, 2048);
static struct k_thread decide_thread, net_rx_thread, status_tx_thread;

int main(void)
{
	LOG_INF("Wingman safety supervisor, %u Hz cycle counter", sys_clock_hw_cycles_per_sec());

	wm_sup_init(&g_sup, NULL);
	wm_meas_reset();
	if (last_reset_was_watchdog()) {
		g_sup.sticky_faults |= WM_FAULT_WDT_RESET;
		LOG_WRN("last reset was caused by the WATCHDOG");
	}

	peer.sin_family = AF_INET;
	peer.sin_port = htons(WM_PORT_STATUS);
	zsock_inet_pton(AF_INET, WM_DEFAULT_PEER_IP, &peer.sin_addr);

	outputs_init();
	k_timer_start(&led_timer, K_MSEC(62), K_MSEC(62));

	k_thread_create(&decide_thread, decide_stack, K_THREAD_STACK_SIZEOF(decide_stack),
			decide_fn, NULL, NULL, NULL, PRIO_DECIDE, 0, K_NO_WAIT);
	k_thread_name_set(&decide_thread, "decide");
	k_thread_create(&status_tx_thread, status_tx_stack, K_THREAD_STACK_SIZEOF(status_tx_stack),
			status_tx_fn, NULL, NULL, NULL, PRIO_STATUS_TX, 0, K_NO_WAIT);
	k_thread_name_set(&status_tx_thread, "status_tx");
	k_thread_create(&net_rx_thread, net_rx_stack, K_THREAD_STACK_SIZEOF(net_rx_stack),
			net_rx_fn, NULL, NULL, NULL, PRIO_NET_RX, 0, K_NO_WAIT);
	k_thread_name_set(&net_rx_thread, "net_rx");

	k_spinlock_key_t key = k_spin_lock(&g_lock);

	wm_sup_link_ready(&g_sup);
	k_spin_unlock(&g_lock, key);
	atomic_set(&led_state, WM_WAIT_LINK);
	LOG_INF("state BOOT -> WAIT_LINK, waiting for perception on udp :%d", WM_PORT_PERCEPTION);

	k_timer_start(&hb_timer, K_MSEC(10), K_MSEC(10));
	return 0;
}
