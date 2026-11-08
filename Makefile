PREFIX ?= /usr/local
bindir ?= $(PREFIX)/bin
DESTDIR ?=

.PHONY: all install uninstall test

all:
	@:

install:
	install -d "$(DESTDIR)$(bindir)"
	install -m 0755 h-app/h-agent "$(DESTDIR)$(bindir)/h-agent"
	install -m 0755 h-app/seedProfile "$(DESTDIR)$(bindir)/seedProfile"
	install -m 0755 h-app/setupConfigDir "$(DESTDIR)$(bindir)/setupConfigDir"
	install -m 0755 h-app/h-agent-profile-lib.sh "$(DESTDIR)$(bindir)/h-agent-profile-lib.sh"
	install -m 0755 h-app/h-agent-capture "$(DESTDIR)$(bindir)/h-agent-capture"
	install -m 0755 h-app/h-agent-web "$(DESTDIR)$(bindir)/h-agent-web"
	install -m 0644 h-app/h_agent_web.py "$(DESTDIR)$(bindir)/h_agent_web.py"

uninstall:
	rm -f "$(DESTDIR)$(bindir)/h-agent"
	rm -f "$(DESTDIR)$(bindir)/seedProfile"
	rm -f "$(DESTDIR)$(bindir)/setupConfigDir"
	rm -f "$(DESTDIR)$(bindir)/h-agent-profile-lib.sh"
	rm -f "$(DESTDIR)$(bindir)/h-agent-capture"
	rm -f "$(DESTDIR)$(bindir)/h-agent-web"
	rm -f "$(DESTDIR)$(bindir)/h_agent_web.py"

test:
	./h-app/test/h-agent-test
	./h-app/test/h-agent-web-test
	./h-app/test/h-agent-capture-test
	./h-app/test/probe-provider-test
	./h-app/test/install-test
	./h-app/test/profile-test
