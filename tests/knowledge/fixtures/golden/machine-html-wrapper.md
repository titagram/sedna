# <div align="center">Wrapped Machine</div>

<div align="center"><img src="machine.png" alt="Machine avatar"><br>Linux training target</div>

# Enumeration

We ran a service scan and recorded the exposed HTTP endpoint before selecting the next test.

```text
nmap target
```

# Investigation

The scan showed a web service, so we inspected the response and found a routing clue. The wrapper is presentation markup; the observation and decision remain the useful content.
