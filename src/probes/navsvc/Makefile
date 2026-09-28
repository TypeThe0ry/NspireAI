GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include

all: navsvc.tns navmain.tns

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

navmain.o: navsvc.c
	$(GCC) -Wall -W -marm -Os -DIN_MAIN -I$(NAVNET_INCLUDE) -c $< -o $@

navmain.elf: navmain.o
	$(LD) $^ -o $@

navmain.tns: navmain.elf
	$(GENZEHN) --input $< --output $@.zehn --name "navmain" --author "NspireAI" --notice "NavNet service with main parked in TCC_Task_Sleep" --240x320-support true
	make-prg $@.zehn $@
	rm -f $@.zehn
