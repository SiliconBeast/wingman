# Host unit tests for the RT1170 supervisor core (no Zephyr needed).
#   make -C rt1170/tests/host
SRC := ../../src
PROTO := ../../../protocol
CFLAGS := -std=c11 -O2 -Wall -Wextra -Werror -I$(SRC) -I$(PROTO) -fsanitize=address,undefined

test_logic: test_logic.c $(SRC)/wm_logic.c $(SRC)/wm_stats.c $(SRC)/*.h $(PROTO)/wingman_proto.h
	$(CC) $(CFLAGS) -o $@ test_logic.c $(SRC)/wm_logic.c $(SRC)/wm_stats.c -lm
	./$@

clean:
	rm -f test_logic

.PHONY: clean
