GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include

all: scanout.tns

scanout.o: scanout.c
	$(GCC) -Wall -W -marm -Os -I$(NAVNET_INCLUDE) -c $< -o $@

scanout.elf: scanout.o
	$(LD) $^ -o $@

scanout.tns: scanout.elf
	$(GENZEHN) --input $< --output $@.zehn --name "scanout" --author "NspireAI" --notice "Private scan-out orientation probe" --240x320-support true
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
