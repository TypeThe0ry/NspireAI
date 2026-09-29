GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include

all: lcdinfo.tns

lcdinfo.o: lcdinfo.c
	$(GCC) -Wall -W -marm -Os -I$(NAVNET_INCLUDE) -c $< -o $@

lcdinfo.elf: lcdinfo.o
	$(LD) $^ -o $@

lcdinfo.tns: lcdinfo.elf
	$(GENZEHN) --input $< --output $@.zehn --name "lcdinfo" --author "NspireAI" --notice "LCD scan-out probe" --240x320-support true
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
