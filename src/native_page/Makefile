DEBUG ?= FALSE
GCC = nspire-gcc
AS = nspire-as
LD = nspire-ld
GENZEHN = genzehn
LUNA ?= $(shell nspire-tools path 2>/dev/null)/tools/luna/luna
GCCFLAGS = -Wall -W -marm
ifeq ($(DEBUG),FALSE)
GCCFLAGS += -Os
else
GCCFLAGS += -O0 -g
endif

OBJS = nspire_ai_nav.o
EXE = nspire_ai_nav.luax
ZEHNFLAGS = --name "nspire_ai_nav" --240x320-support true

.PHONY: all clean
all: $(EXE).tns nspire_ai.tns

nspire_ai_nav.o: nspire_ai_nav.c
	$(GCC) $(GCCFLAGS) -c $< -o $@

$(EXE).elf: $(OBJS)
	$(LD) $^ -o $@

$(EXE).tns: $(EXE).elf
	$(GENZEHN) --input $< --output $@.zehn $(ZEHNFLAGS)
	make-prg $@.zehn $@
	rm -f $@.zehn

nspire_ai.tns: nspire_ai_page.lua
	$(LUNA) $< $@

clean:
	rm -f $(OBJS) $(EXE).elf $(EXE).tns $(EXE).zehn nspire_ai.tns
