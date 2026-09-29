GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include

all: shadowfind.tns

shadowfind.o: shadowfind.c
	$(GCC) -Wall -W -marm -Os -I$(NAVNET_INCLUDE) -c $< -o $@

shadowfind.elf: shadowfind.o
	$(LD) $^ -o $@

shadowfind.tns: shadowfind.elf
	$(GENZEHN) --input $< --output $@.zehn --name "shadowfind" --author "NspireAI" --notice "OS composition buffer finder" --240x320-support true
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
