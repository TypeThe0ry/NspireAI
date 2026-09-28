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
all: nspire_ai.tns nspire_ai_autotest.tns

nspire_ai.o: page.c
	$(GCC) $(CFLAGS) -c $< -o $@

nspire_ai_autotest.o: page.c
	$(GCC) $(CFLAGS) -DPAGE_AUTOTEST -DSERVICE_ID=0x5010 -c $< -o $@

%.elf: %.o
	$(LD) $^ -o $@

%.tns: %.elf
	$(GENZEHN) --input $< --output $@.zehn --name "NspireAI" $(ZEHNFLAGS) --notice "NspireAI chat page (resident task + NavNet service)"
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
