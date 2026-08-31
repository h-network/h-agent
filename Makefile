PREFIX ?= /usr/local
bindir ?= $(PREFIX)/bin
DESTDIR ?=

.PHONY: all install uninstall test

all:
	@:

install:
	install -d "$(DESTDIR)$(bindir)"
	install -m 0755 h-agent "$(DESTDIR)$(bindir)/h-agent"
	install -m 0755 seedProfile "$(DESTDIR)$(bindir)/seedProfile"
	install -m 0755 setupConfigDir "$(DESTDIR)$(bindir)/setupConfigDir"
	install -m 0755 h-agent-profile-lib.sh "$(DESTDIR)$(bindir)/h-agent-profile-lib.sh"

uninstall:
	rm -f "$(DESTDIR)$(bindir)/h-agent"
	rm -f "$(DESTDIR)$(bindir)/seedProfile"
	rm -f "$(DESTDIR)$(bindir)/setupConfigDir"
	rm -f "$(DESTDIR)$(bindir)/h-agent-profile-lib.sh"

test:
	./test/h-agent-test
	./test/probe-provider-test
	./test/install-test
	./test/profile-test
