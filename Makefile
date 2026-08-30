PREFIX ?= /usr/local
bindir ?= $(PREFIX)/bin
DESTDIR ?=

.PHONY: all install uninstall test

all:
	@:

install:
	install -d "$(DESTDIR)$(bindir)"
	install -m 0755 h-agent "$(DESTDIR)$(bindir)/h-agent"

uninstall:
	rm -f "$(DESTDIR)$(bindir)/h-agent"

test:
	./test/h-agent-test
	./test/install-test
