GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include
FONT_INCLUDE ?= /sdk/thirdparty/nspire-io/common
CFLAGS = -Wall -W -marm -Os -I$(NAVNET_INCLUDE) -I$(FONT_INCLUDE)
ZEHNFLAGS = --author "NspireAI" --240x320-support true --uses-lcd-blit true

# nspire_ai.tns: the chat page (NavNet service 0x5001).
# nspire_ai_dev.tns: the same page on a spare service id, so a change can be
# tried next to the installed page; run the bridge with
# NSPIRE_CLIENT_SERVICE_ID=$(DEV_SERVICE_ID).
DEV_SERVICE_ID ?= 0x5012
.PRECIOUS: %.elf %.o
all: nspire_ai.tns nspire_ai_dev.tns

nspire_ai.o: page.c
	$(GCC) $(CFLAGS) -c $< -o $@

nspire_ai_dev.o: page.c
	$(GCC) $(CFLAGS) -DSERVICE_ID=$(DEV_SERVICE_ID) -c $< -o $@

%.elf: %.o
	$(LD) $^ -o $@

%.tns: %.elf
	$(GENZEHN) --input $< --output $@.zehn --name "NspireAI" $(ZEHNFLAGS) --notice "NspireAI chat page"
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
