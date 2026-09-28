GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
NAVNET_INCLUDE ?= /sdk/NavNet/include

all: navtask.tns

navtask.o: navtask.c
	$(GCC) -Wall -W -marm -Os -I$(NAVNET_INCLUDE) -c $< -o $@

navtask.elf: navtask.o
	$(LD) $^ -o $@

navtask.tns: navtask.elf
	$(GENZEHN) --input $< --output $@.zehn --name "navtask" --author "NspireAI" --notice "NavNet from resident task probe" --240x320-support true
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
