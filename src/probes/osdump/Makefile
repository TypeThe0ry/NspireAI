GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include

all: osdump.tns

osdump.o: osdump.c
	$(GCC) -Wall -W -marm -Os -I$(NAVNET_INCLUDE) -c $< -o $@

osdump.elf: osdump.o
	$(LD) $^ -o $@

osdump.tns: osdump.elf
	$(GENZEHN) --input $< --output $@.zehn --name "osdump" --author "NspireAI" --notice "OS code range dump" --240x320-support true
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
