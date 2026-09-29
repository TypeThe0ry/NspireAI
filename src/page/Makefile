GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include
FONT_INCLUDE ?= /sdk/thirdparty/nspire-io/common
CFLAGS = -Wall -W -marm -Os -I$(NAVNET_INCLUDE) -I$(FONT_INCLUDE)
ZEHNFLAGS = --author "NspireAI" --240x320-support true --uses-lcd-blit true

# nspire_ai.tns: the chat page.  nspire_ai_autotest.tns additionally sends a
# prompt by itself once linked and acknowledges the response, so the host
# can verify the round trip without anyone typing.
.PRECIOUS: %.elf %.o
all: nspire_ai.tns nspire_ai_autotest.tns nspire_ai_dev.tns nspire_ai_dev2.tns

nspire_ai.o: page.c
	$(GCC) $(CFLAGS) -c $< -o $@

# Same page on a spare service id with its own registry entry: loads next to
# a resident nspire_ai without a reset, for trying a change on the handheld.
# Bump DEV_SERVICE_ID for each try within one boot.
DEV_SERVICE_ID ?= 0x5012
AUTOTEST_SERVICE_ID ?= 0x5010
nspire_ai_dev.o: page.c
	$(GCC) $(CFLAGS) -DSERVICE_ID=$(DEV_SERVICE_ID) -c $< -o $@

# dev2: same as dev but leaves the keypad IRQ to the OS (tests whether the
# masked keypad IRQ is what stalls the parked UI task).
DEV2_SERVICE_ID ?= 0x5013
nspire_ai_dev2.o: page.c
	$(GCC) $(CFLAGS) -DSERVICE_ID=$(DEV2_SERVICE_ID) -DPAGE_KEEP_KEYPAD_IRQ -c $< -o $@

nspire_ai_autotest.o: page.c
	$(GCC) $(CFLAGS) -DPAGE_AUTOTEST -DSERVICE_ID=$(AUTOTEST_SERVICE_ID) -c $< -o $@

%.elf: %.o
	$(LD) $^ -o $@

%.tns: %.elf
	$(GENZEHN) --input $< --output $@.zehn --name "NspireAI" $(ZEHNFLAGS) --notice "NspireAI chat page (resident task + NavNet service)"
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
