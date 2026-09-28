GCC = nspire-gcc
LD = nspire-ld
GENZEHN = genzehn
# STEP=plain: markers + busy-wait only.  STEP=lcd: also NGC lcd_init/teardown.
# STEP=gc: lcd plus global GC and one drawn frame.
STEP ?= plain

CFLAGS = -Wall -W -marm -Os
ZEHNFLAGS = --name "marker" --author "NspireAI" --240x320-support true
ifeq ($(STEP),gc)
CFLAGS += -DPROBE_GC
ZEHNFLAGS += --notice "USB baseline marker probe + LCD + GC frame" --uses-lcd-blit true
OUT = marker_gc
else ifeq ($(STEP),lcd)
CFLAGS += -DPROBE_LCD
ZEHNFLAGS += --notice "USB baseline marker probe + LCD" --uses-lcd-blit true
OUT = marker_lcd
else
ZEHNFLAGS += --notice "USB baseline marker probe"
OUT = marker
endif

all: $(OUT).tns

$(OUT).o: marker.c
	$(GCC) $(CFLAGS) -c $< -o $@

$(OUT).elf: $(OUT).o
	$(LD) $^ -o $@

$(OUT).tns: $(OUT).elf
	$(GENZEHN) --input $< --output $@.zehn $(ZEHNFLAGS)
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f *.o *.elf *.tns *.zehn
