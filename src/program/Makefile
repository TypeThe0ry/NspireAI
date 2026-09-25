DEBUG ?= FALSE
UI_NGC ?= FALSE
NGC_PROBE ?= FALSE
NGC_PROBE_STAGE ?= 0
NGC_AUTO_TRANSPORT ?= FALSE
NGC_CPU_IRQ ?= FALSE
NGC_USB_IRQ_WINDOW ?= FALSE
NGC_USB_IRQ_MENU ?= FALSE

GCC = nspire-gcc
AS = nspire-as
LD = nspire-ld
ARM_GCC = arm-none-eabi-gcc
GENZEHN = genzehn
SDK_ROOT ?= /sdk

NAVNET_INCLUDE ?= /sdk/NavNet/include
GCCFLAGS = -Wall -W -marm -I$(NAVNET_INCLUDE)
ZEHNFLAGS = --name "NspireAI" --author "NspireAI" --240x320-support true

ifeq ($(DEBUG),FALSE)
GCCFLAGS += -Os
else
GCCFLAGS += -O0 -g
endif

ifeq ($(UI_NGC),TRUE)
GCCFLAGS += -DNSPIRE_UI_NGC
ifeq ($(NGC_CPU_IRQ),TRUE)
GCCFLAGS += -DNSPIRE_NGC_CPU_IRQ
endif
ifeq ($(NGC_USB_IRQ_WINDOW),TRUE)
GCCFLAGS += -DNSPIRE_NGC_USB_IRQ_WINDOW
ZEHNFLAGS += --notice "NGC/RTC UI; experimental IRQ window; USB transport remains gated" --uses-lcd-blit true
else ifeq ($(NGC_USB_IRQ_MENU),TRUE)
GCCFLAGS += -DNSPIRE_NGC_USB_IRQ_MENU
ZEHNFLAGS += --notice "NGC/RTC UI; Menu-gated experimental IRQ window" --uses-lcd-blit true
else ifeq ($(NGC_AUTO_TRANSPORT),TRUE)
GCCFLAGS += -DNSPIRE_NGC_AUTO_TRANSPORT
ZEHNFLAGS += --notice "NGC/RTC UI; USB transport explicitly armed by build" --uses-lcd-blit true
else
ZEHNFLAGS += --notice "NGC/RTC UI; USB idle until Menu; timer-neutral" --uses-lcd-blit true
endif
ifeq ($(NGC_PROBE),TRUE)
GCCFLAGS += -DNSPIRE_NGC_PROBE -DNSPIRE_NGC_PROBE_STAGE=$(NGC_PROBE_STAGE)
endif
else
ZEHNFLAGS += --notice "Standalone Ndless NavNet chat"
endif

EXE = nspire_ai
OBJS = main.o

.PHONY: all clean

all: $(EXE).tns

main.o: main.c nav_os_call.h nav_clock.h nav_fragment.h nav_irq_window.h ui_ngc.h
	$(GCC) $(GCCFLAGS) -c $< -o $@

ifneq ($(UI_NGC),TRUE)
$(EXE).elf: $(OBJS)
	$(LD) $^ -o $@
else

$(EXE).elf: $(OBJS)
	# Use the SDK linker wrapper so crt objects, standard libraries, PIC
	# veneers, and --emit-relocs stay in the same combination as Ndless's
	# shipped samples.  The hand-written arm-gcc link produced a second .got
	# after .bss; genzehn warned about that layout and the handheld rejected
	# the resulting package as an unsupported document.
	$(LD) $^ -o $@
endif

$(EXE).tns: $(EXE).elf
	$(GENZEHN) --input $< --output $@.zehn $(ZEHNFLAGS)
	make-prg $@.zehn $@
	rm -f $@.zehn

clean:
	rm -f $(OBJS) $(EXE).elf $(EXE).tns $(EXE).tns.zehn
