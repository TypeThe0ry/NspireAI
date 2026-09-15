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

OBJS = nspire_ai.o
EXE = nspire_ai.luax
ZEHNFLAGS = --name "nspire_ai" --240x320-support true

.PHONY: all clean
all: $(EXE).tns

%.o: %.c
	$(GCC) $(GCCFLAGS) -c $< -o $@

$(EXE).elf: $(OBJS)
	$(LD) $^ -o $@

$(EXE).tns: $(EXE).elf
	$(GENZEHN) --input $< --output $@.zehn $(ZEHNFLAGS)
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f $(OBJS) $(EXE).elf $(EXE).tns $(EXE).tns.zehn
