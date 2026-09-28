GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include

all: navsvc.tns

navsvc.o: navsvc.c
	$(GCC) -Wall -W -marm -Os -I$(NAVNET_INCLUDE) -c $< -o $@

navsvc.elf: navsvc.o
	$(LD) $^ -o $@

navsvc.tns: navsvc.elf
	$(GENZEHN) --input $< --output $@.zehn --name "navsvc" --author "NspireAI" --notice "NavNet service from resident task probe" --240x320-support true
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
