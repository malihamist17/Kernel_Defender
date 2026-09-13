# Makefile — standard out-of-tree Linux kernel module build.
# Run from this directory: `make` (build), `make clean` (remove build
# artifacts), then load/unload manually with insmod/rmmod (see the header
# comment in defender_module.c).

obj-m += defender_module.o

KDIR ?= /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
