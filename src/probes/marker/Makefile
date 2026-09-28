GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn

all: marker.tns

marker.o: marker.c
	$(GCC) -Wall -W -marm -Os -c $< -o $@

marker.elf: marker.o
	$(LD) $^ -o $@

marker.tns: marker.elf
	$(GENZEHN) --input $< --output $@.zehn --name "marker" --author "NspireAI" --notice "USB baseline marker probe" --240x320-support true
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f marker.o marker.elf marker.tns marker.tns.zehn
