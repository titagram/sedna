Fingerprinting | Hack The Box Academy

[![HTB Academy Logo](/app/htb-academy-logo.svg)](/app/dashboard)

*   [
    
    Dashboard
    
    
    
    ](/app/dashboard "Dashboard")
*   [
    
    Library
    
    
    
    ](/app/library "Library")
*   Resources
    

[](https://roadmap.hackthebox.com/changelog?labels=academy)

434

[

Upgrade

](/app/billing)

![user avatar](data:image/svg+xml,%3c?xml%20version='1.0'%20encoding='utf-8'?%3e%3c!--%20Generator:%20Adobe%20Illustrator%2024.1.2,%20SVG%20Export%20Plug-In%20.%20SVG%20Version:%206.00%20Build%200)%20--%3e%3csvg%20version='1.1'%20id='Layer_1'%20xmlns='http://www.w3.org/2000/svg'%20xmlns:xlink='http://www.w3.org/1999/xlink'%20x='0px'%20y='0px'%20viewBox='0%200%2032%2033'%20style='enable-background:new%200%200%2032%2033;'%20xml:space='preserve'%3e%3cstyle%20type='text/css'%3e%20.st0{fill:%239FEF00;}%20%3c/style%3e%3cdesc%3eCreated%20with%20Sketch.%3c/desc%3e%3cpath%20class='st0'%20d='M29.6,9.3C29.6,9.3,29.6,9.3,29.6,9.3c0-0.3-0.1-0.6-0.4-0.8c0,0,0,0,0,0c0,0,0,0-0.1-0.1c0,0-0.1,0-0.1-0.1%20c0,0,0,0,0,0L16.6,1.2c0,0-0.1,0-0.1,0C16.3,1,16.1,1,15.9,1c-0.1,0-0.2,0-0.3,0.1c-0.1,0-0.1,0.1-0.2,0.1L3,8.3c0,0,0,0,0,0%20c0,0,0,0,0,0c0,0,0,0,0,0C2.8,8.4,2.8,8.5,2.7,8.6c0,0,0,0,0,0C2.5,8.8,2.4,9,2.4,9.3c0,0,0,0,0,0c0,0,0,0,0,0v14.3%20c0,0.4,0.2,0.8,0.6,1l12.4,7.2c0,0,0,0,0.1,0c0,0,0,0,0,0c0.1,0,0.1,0.1,0.2,0.1c0,0,0,0,0,0c0.1,0,0.2,0,0.2,0c0.1,0,0.2,0,0.2,0%20c0,0,0,0,0,0c0.1,0,0.1,0,0.2-0.1c0,0,0,0,0,0c0,0,0,0,0.1,0L29,24.7c0.4-0.2,0.6-0.6,0.6-1L29.6,9.3C29.6,9.4,29.6,9.3,29.6,9.3z%20M7.3,8.9L15.7,4c0.2-0.1,0.4-0.1,0.5,0l8.4,4.9c0.4,0.2,0.4,0.7,0,0.9l-8.4,4.9c-0.2,0.1-0.4,0.1-0.5,0L7.3,9.8%20C6.9,9.6,6.9,9.1,7.3,8.9z%20M14.5,27.4c0,0.4-0.4,0.7-0.8,0.5L5.3,23C5.1,22.9,5,22.7,5,22.5v-9.7c0-0.4,0.4-0.7,0.8-0.5l8.4,4.9%20c0.2,0.1,0.3,0.3,0.3,0.5V27.4z%20M27,22.5c0,0.2-0.1,0.4-0.3,0.5l-8.4,4.9c-0.4,0.2-0.8-0.1-0.8-0.5v-9.7c0-0.2,0.1-0.4,0.3-0.5%20l8.4-4.9c0.4-0.2,0.8,0.1,0.8,0.5V22.5z'/%3e%3c/svg%3e)

![Information Gathering - Web Edition](https://cdn.services-k8s.prod.aws.htb.systems/content/modules/avatar/9e65e8b3-ec50-4c29-9823-f0be48c20cb2.png)

[Information Gathering - Web Edition 100%](/app/module/144)

* * *

* * *

*   English (Original)
    
*   * * *
    
    * * *
    

Section 11 / 19

[Go to Questions](/app/module/144/section/3075#questions-list)

Fingerprinting
==============

* * *

Fingerprinting focuses on extracting technical details about the technologies powering a website or web application. Similar to how a fingerprint uniquely identifies a person, the digital signatures of web servers, operating systems, and software components can reveal critical information about a target's infrastructure and potential security weaknesses. This knowledge empowers attackers to tailor attacks and exploit vulnerabilities specific to the identified technologies.

Fingerprinting serves as a cornerstone of web reconnaissance for several reasons:

*   `Targeted Attacks`: By knowing the specific technologies in use, attackers can focus their efforts on exploits and vulnerabilities that are known to affect those systems. This significantly increases the chances of a successful compromise.
*   `Identifying Misconfigurations`: Fingerprinting can expose misconfigured or outdated software, default settings, or other weaknesses that might not be apparent through other reconnaissance methods.
*   `Prioritising Targets`: When faced with multiple potential targets, fingerprinting helps prioritise efforts by identifying systems more likely to be vulnerable or hold valuable information.
*   `Building a Comprehensive Profile`: Combining fingerprint data with other reconnaissance findings creates a holistic view of the target's infrastructure, aiding in understanding its overall security posture and potential attack vectors.

Fingerprinting Techniques
-------------------------

There are several techniques used for web server and technology fingerprinting:

*   `Banner Grabbing`: Banner grabbing involves analysing the banners presented by web servers and other services. These banners often reveal the server software, version numbers, and other details.
*   `Analysing HTTP Headers`: HTTP headers transmitted with every web page request and response contain a wealth of information. The `Server` header typically discloses the web server software, while the `X-Powered-By` header might reveal additional technologies like scripting languages or frameworks.
*   `Probing for Specific Responses`: Sending specially crafted requests to the target can elicit unique responses that reveal specific technologies or versions. For example, certain error messages or behaviours are characteristic of particular web servers or software components.
*   `Analysing Page Content`: A web page's content, including its structure, scripts, and other elements, can often provide clues about the underlying technologies. There may be a copyright header that indicates specific software being used, for example.

A variety of tools exist that automate the fingerprinting process, combining various techniques to identify web servers, operating systems, content management systems, and other technologies:

Tool

Description

Features

`Wappalyzer`

Browser extension and online service for website technology profiling.

Identifies a wide range of web technologies, including CMSs, frameworks, analytics tools, and more.

`BuiltWith`

Web technology profiler that provides detailed reports on a website's technology stack.

Offers both free and paid plans with varying levels of detail.

`WhatWeb`

Command-line tool for website fingerprinting.

Uses a vast database of signatures to identify various web technologies.

`Nmap`

Versatile network scanner that can be used for various reconnaissance tasks, including service and OS fingerprinting.

Can be used with scripts (NSE) to perform more specialised fingerprinting.

`Netcraft`

Offers a range of web security services, including website fingerprinting and security reporting.

Provides detailed reports on a website's technology, hosting provider, and security posture.

`wafw00f`

Command-line tool specifically designed for identifying Web Application Firewalls (WAFs).

Helps determine if a WAF is present and, if so, its type and configuration.

Fingerprinting inlanefreight.com
--------------------------------

Let's apply our fingerprinting knowledge to uncover the digital DNA of our purpose-built host, `inlanefreight.com`. We'll leverage both manual and automated techniques to gather information about its web server, technologies, and potential vulnerabilities.

### Banner Grabbing

Our first step is to gather information directly from the web server itself. We can do this using the `curl` command with the `-I` flag (or `--head`) to fetch only the HTTP headers, not the entire page content.

        shellsession
`titagram@htb[/htb]$ curl -I inlanefreight.com`

The output will include the server banner, revealing the web server software and version number:

        shellsession
`titagram@htb[/htb]$ curl -I inlanefreight.com  HTTP/1.1 301 Moved Permanently Date: Fri, 31 May 2024 12:07:44 GMT Server: Apache/2.4.41 (Ubuntu) Location: https://inlanefreight.com/ Content-Type: text/html; charset=iso-8859-1`

In this case, we see that `inlanefreight.com` is running on `Apache/2.4.41`, specifically the `Ubuntu` version. This information is our first clue, hinting at the underlying technology stack. It's also trying to redirect to `https://inlanefreight.com/` so grab those banners too

        shellsession
`titagram@htb[/htb]$ curl -I https://inlanefreight.com  HTTP/1.1 301 Moved Permanently Date: Fri, 31 May 2024 12:12:12 GMT Server: Apache/2.4.41 (Ubuntu) X-Redirect-By: WordPress Location: https://www.inlanefreight.com/ Content-Type: text/html; charset=UTF-8`

We now get a really interesting header, the server is trying to redirect us again, but this time we see that it's `WordPress` that is doing the redirection to `https://www.inlanefreight.com/`

        shellsession
`titagram@htb[/htb]$ curl -I https://www.inlanefreight.com  HTTP/1.1 200 OK Date: Fri, 31 May 2024 12:12:26 GMT Server: Apache/2.4.41 (Ubuntu) Link: <https://www.inlanefreight.com/index.php/wp-json/>; rel="https://api.w.org/" Link: <https://www.inlanefreight.com/index.php/wp-json/wp/v2/pages/7>; rel="alternate"; type="application/json" Link: <https://www.inlanefreight.com/>; rel=shortlink Content-Type: text/html; charset=UTF-8`

A few more interesting headers, including an interesting path that contains `wp-json`. The `wp-` prefix is common to WordPress.

### Wafw00f

`Web Application Firewalls` (`WAFs`) are security solutions designed to protect web applications from various attacks. Before proceeding with further fingerprinting, it's crucial to determine if `inlanefreight.com` employs a WAF, as it could interfere with our probes or potentially block our requests.

To detect the presence of a WAF, we'll use the `wafw00f` tool. To install `wafw00f`, you can use pip3:

        shellsession
`titagram@htb[/htb]$ pip3 install git+https://github.com/EnableSecurity/wafw00f`

Once it's installed, pass the domain you want to check as an argument to the tool:

        shellsession
```titagram@htb[/htb]$ wafw00f inlanefreight.com                  ______                /      \               (  W00f! )                \  ____/                ,,    __            404 Hack Not Found            |`-.__   / /                      __     __            /"  _/  /_/                       \ \   / /           *===*    /                          \ \_/ /  405 Not Allowed          /     )__//                           \   /     /|  /     /---`                        403 Forbidden     \\/`   \ |                                 / _ \     `\    /_\\_              502 Bad Gateway  / / \ \  500 Internal Error       `_____``-`                             /_/   \_\                          ~ WAFW00F : v2.2.0 ~         The Web Application Firewall Fingerprinting Toolkit      [*] Checking https://inlanefreight.com [+] The site https://inlanefreight.com is behind Wordfence (Defiant) WAF. [~] Number of requests: 2```

The `wafw00f` scan on `inlanefreight.com` reveals that the website is protected by the `Wordfence Web Application Firewall` (`WAF`), developed by Defiant.

This means the site has an additional security layer that could block or filter our reconnaissance attempts. In a real-world scenario, it would be crucial to keep this in mind as you proceed with further investigation, as you might need to adapt techniques to bypass or evade the WAF's detection mechanisms.

### Nikto

`Nikto` is a powerful open-source web server scanner. In addition to its primary function as a vulnerability assessment tool, `Nikto's` fingerprinting capabilities provide insights into a website's technology stack.

`Nikto` is pre-installed on pwnbox, but if you need to install it, you can run the following commands:

        shellsession
`titagram@htb[/htb]$ sudo apt update && sudo apt install -y perl titagram@htb[/htb]$ git clone https://github.com/sullo/nikto titagram@htb[/htb]$ cd nikto/program titagram@htb[/htb]$ chmod +x ./nikto.pl`

To scan `inlanefreight.com` using `Nikto`, only running the fingerprinting modules, execute the following command:

        shellsession
`titagram@htb[/htb]$ nikto -h inlanefreight.com -Tuning b`

The `-h` flag specifies the target host. The `-Tuning b` flag tells `Nikto` to only run the Software Identification modules.

`Nikto` will then initiate a series of tests, attempting to identify outdated software, insecure files or configurations, and other potential security risks.

        shellsession
`titagram@htb[/htb]$ nikto -h inlanefreight.com -Tuning b  - Nikto v2.5.0 --------------------------------------------------------------------------- + Multiple IPs found: 134.209.24.248, 2a03:b0c0:1:e0::32c:b001 + Target IP:          134.209.24.248 + Target Hostname:    www.inlanefreight.com + Target Port:        443 --------------------------------------------------------------------------- + SSL Info:        Subject:  /CN=inlanefreight.com                    Altnames: inlanefreight.com, www.inlanefreight.com                    Ciphers:  TLS_AES_256_GCM_SHA384                    Issuer:   /C=US/O=Let's Encrypt/CN=R3 + Start Time:         2024-05-31 13:35:54 (GMT0) --------------------------------------------------------------------------- + Server: Apache/2.4.41 (Ubuntu) + /: Link header found with value: ARRAY(0x558e78790248). See: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Link + /: The site uses TLS and the Strict-Transport-Security HTTP header is not defined. See: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Strict-Transport-Security + /: The X-Content-Type-Options header is not set. This could allow the user agent to render the content of the site in a different fashion to the MIME type. See: https://www.netsparker.com/web-vulnerability-scanner/vulnerabilities/missing-content-type-header/ + /index.php?: Uncommon header 'x-redirect-by' found, with contents: WordPress. + No CGI Directories found (use '-C all' to force check all possible dirs) + /: The Content-Encoding header is set to "deflate" which may mean that the server is vulnerable to the BREACH attack. See: http://breachattack.com/ + Apache/2.4.41 appears to be outdated (current is at least 2.4.59). Apache 2.2.34 is the EOL for the 2.x branch. + /: Web Server returns a valid response with junk HTTP methods which may cause false positives. + /license.txt: License file found may identify site software. + /: A Wordpress installation was found. + /wp-login.php?action=register: Cookie wordpress_test_cookie created without the httponly flag. See: https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies + /wp-login.php:X-Frame-Options header is deprecated and has been replaced with the Content-Security-Policy HTTP header with the frame-ancestors directive instead. See: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Frame-Options + /wp-login.php: Wordpress login found. + 1316 requests: 0 error(s) and 12 item(s) reported on remote host + End Time:           2024-05-31 13:47:27 (GMT0) (693 seconds) --------------------------------------------------------------------------- + 1 host(s) tested`

The reconnaissance scan on `inlanefreight.com` reveals several key findings:

*   `IPs`: The website resolves to both IPv4 (`134.209.24.248`) and IPv6 (`2a03:b0c0:1:e0::32c:b001`) addresses.
*   `Server Technology`: The website runs on `Apache/2.4.41 (Ubuntu)`
*   `WordPress Presence`: The scan identified a WordPress installation, including the login page (`/wp-login.php`). This suggests the site might be a potential target for common WordPress-related exploits.
*   `Information Disclosure`: The presence of a `license.txt` file could reveal additional details about the website's software components.
*   `Headers`: Several non-standard or insecure headers were found, including a missing `Strict-Transport-Security` header and a potentially insecure `x-redirect-by` header.

Connect to HTB
--------------

Pwnbox

Your own web-based Parrot Linux instance to play our labs.

VPN

Download your files and connect within your own environment

Switching Pwnbox location will terminate the spawned Pwnbox.

Pwnbox Location

DE

30ms

Start Pwnbox

∞spawns left

Offline

Target(s)
---------

Spawn the target system to get IPs and answer questions

Spawn the target system

Enable step-by-step solutions

PRO

vHosts needed for these questions:

*   app.inlanefreight.local
*   dev.inlanefreight.local

Answer questions in English to ensure an accurate response.

*   Question 1
    ----------
    
    +1
    
    +30
    
*   Question 2
    ----------
    
    +1
    
    +30
    
*   Question 3
    ----------
    
    +1
    
    +30
    

Previous

Section 11 / 19

+15

Next

Completed

![adblock modal image](/app/images/svg/warning.svg)

### Ad Blocker Detected

Please whitelist our site in your adblocker otherwise,  
Technical Support chat may not load.

I understand

Close

### Cheatsheet

The cheat sheet is a useful command reference for this module.

Cheat Sheet
===========

* * *

Web reconnaissance is the first step in any security assessment or penetration testing engagement. It's akin to a detective's initial investigation, meticulously gathering clues and evidence about a target before formulating a plan of action. In the digital realm, this translates to accumulating information about a website or web application to identify potential vulnerabilities, security misconfigurations, and valuable assets.

The primary goals of web reconnaissance revolve around gaining a comprehensive understanding of the target's digital footprint. This includes:

*   `Identifying Assets`: Discovering all associated domains, subdomains, and IP addresses provides a map of the target's online presence.
*   `Uncovering Hidden Information`: Web reconnaissance aims to uncover directories, files, and technologies that are not readily apparent and could serve as entry points for an attacker.
*   `Analyzing the Attack Surface`: By identifying open ports, running services, and software versions, you can assess the potential vulnerabilities and weaknesses of the target.
*   `Gathering Intelligence`: Collecting information about employees, email addresses, and technologies used can aid in social engineering attacks or identifying specific vulnerabilities associated with certain software.

Web reconnaissance can be conducted using either active or passive techniques, each with its own advantages and drawbacks:

Type

Description

Risk of Detection

Examples

Active Reconnaissance

Involves directly interacting with the target system, such as sending probes or requests.

Higher

Port scanning, vulnerability scanning, network mapping

Passive Reconnaissance

Gathers information without directly interacting with the target, relying on publicly available data.

Lower

Search engine queries, WHOIS lookups, DNS enumeration, web archive analysis, social media

WHOIS
-----

WHOIS is a query and response protocol used to retrieve information about domain names, IP addresses, and other internet resources. It's essentially a directory service that details who owns a domain, when it was registered, contact information, and more. In the context of web reconnaissance, WHOIS lookups can be a valuable source of information, potentially revealing the identity of the website owner, their contact information, and other details that could be used for further investigation or social engineering attacks.

For example, if you wanted to find out who owns the domain `example.com`, you could run the following command in your terminal:

        bash
`whois example.com`

This would return a wealth of information, including the registrar, registration, and expiration dates, nameservers, and contact information for the domain owner.

However, it's important to note that WHOIS data can be inaccurate or intentionally obscured, so it's always wise to verify the information from multiple sources. Privacy services can also mask the true owner of a domain, making it more difficult to obtain accurate information through WHOIS.

DNS
---

The Domain Name System (DNS) functions as the internet's GPS, translating user-friendly domain names into the numerical IP addresses computers use to communicate. Like GPS converting a destination's name into coordinates, DNS ensures your browser reaches the correct website by matching its name with its IP address. This eliminates memorizing complex numerical addresses, making web navigation seamless and efficient.

The `dig` command allows you to query DNS servers directly, retrieving specific information about domain names. For instance, if you want to find the IP address associated with `example.com`, you can execute the following command:

        bash
`dig example.com A`

This command instructs `dig` to query the DNS for the `A` record (which maps a hostname to an IPv4 address) of `example.com`. The output will typically include the requested IP address, along with additional details about the query and response. By mastering the `dig` command and understanding the various DNS record types, you gain the ability to extract valuable information about a target's infrastructure and online presence.

DNS servers store various types of records, each serving a specific purpose:

Record Type

Description

A

Maps a hostname to an IPv4 address.

AAAA

Maps a hostname to an IPv6 address.

CNAME

Creates an alias for a hostname, pointing it to another hostname.

MX

Specifies mail servers responsible for handling email for the domain.

NS

Delegates a DNS zone to a specific authoritative name server.

TXT

Stores arbitrary text information.

SOA

Contains administrative information about a DNS zone.

Subdomains
----------

Subdomains are essentially extensions of a primary domain name, often used to organize different sections or services within a website. For example, a company might use `mail.example.com` for their email server or `blog.example.com` for their blog.

From a reconnaissance perspective, subdomains are incredibly valuable. They can expose additional attack surfaces, reveal hidden services, and provide clues about the internal structure of a target's network. Subdomains might host development servers, staging environments, or even forgotten applications that haven't been properly secured.

The process of discovering subdomains is known as subdomain enumeration. There are two main approaches to subdomain enumeration:

Approach

Description

Examples

`Active Enumeration`

Directly interacts with the target's DNS servers or utilizes tools to probe for subdomains.

Brute-forcing, DNS zone transfers

`Passive Enumeration`

Collects information about subdomains without directly interacting with the target, relying on public sources.

Certificate Transparency (CT) logs, search engine queries

`Active enumeration` can be more thorough but carries a higher risk of detection. Conversely, `passive enumeration` is stealthier but may not uncover all subdomains. Combining both techniques can significantly increase the likelihood of discovering a comprehensive list of subdomains associated with your target, expanding your understanding of their online presence and potential vulnerabilities.

### Subdomain Brute-Forcing

Subdomain brute-forcing is a proactive technique used in web reconnaissance to uncover subdomains that may not be readily apparent through passive methods. It involves systematically generating many potential subdomain names and testing them against the target's DNS server to see if they exist. This approach can unveil hidden subdomains that may host valuable information, development servers, or vulnerable applications.

One of the most versatile tools for subdomain brute-forcing is `dnsenum`. This powerful command-line tool combines various DNS enumeration techniques, including dictionary-based brute-forcing, to uncover subdomains associated with your target.

To use `dnsenum` for subdomain brute-forcing, you'll typically provide it with the target domain and a wordlist containing potential subdomain names. The tool will then systematically query the DNS server for each potential subdomain and report any that exist.

For example, the following command would attempt to brute-force subdomains of `example.com` using a wordlist named `subdomains.txt`:

        bash
`dnsenum example.com -f subdomains.txt`

### Zone Transfers

DNS zone transfers, also known as AXFR (Asynchronous Full Transfer) requests, offer a potential goldmine of information for web reconnaissance. A zone transfer is a mechanism for replicating DNS data across servers. When a zone transfer is successful, it provides a complete copy of the DNS zone file, which contains a wealth of details about the target domain.

This zone file lists all the domain's subdomains, their associated IP addresses, mail server configurations, and other DNS records. This is akin to obtaining a blueprint of the target's DNS infrastructure for a reconnaissance expert.

To attempt a zone transfer, you can use the `dig` command with the `axfr` (full zone transfer) option. For example, to request a zone transfer from the DNS server `ns1.example.com` for the domain `example.com`, you would execute:

        bash
`dig @ns1.example.com example.com axfr`

However, zone transfers are not always permitted. Many DNS servers are configured to restrict zone transfers to authorized secondary servers only. Misconfigured servers, though, may allow zone transfers from any source, inadvertently exposing sensitive information.

### Virtual Hosts

Virtual hosting is a technique that allows multiple websites to share a single IP address. Each website is associated with a unique hostname, which is used to direct incoming requests to the correct site. This can be a cost-effective way for organizations to host multiple websites on a single server, but it can also create a challenge for web reconnaissance.

Since multiple websites share the same IP address, simply scanning the IP won't reveal all the hosted sites. You need a tool that can test different hostnames against the IP address to see which ones respond.

Gobuster is a versatile tool that can be used for various types of brute-forcing, including virtual host discovery. Its `vhost` mode is designed to enumerate virtual hosts by sending requests to the target IP address with different hostnames. If a virtual host is configured for a specific hostname, Gobuster will receive a response from the web server.

To use Gobuster to brute-force virtual hosts, you'll need a wordlist containing potential hostnames. Here's an example command:

        bash
`gobuster vhost -u http://192.0.2.1 -w hostnames.txt`

In this example, `-u` specifies the target IP address, and `-w` specifies the wordlist file. Gobuster will then systematically try each hostname in the wordlist and report any that results in a valid response from the web server.

### Certificate Transparency (CT) Logs

Certificate Transparency (CT) logs offer a treasure trove of subdomain information for passive reconnaissance. These publicly accessible logs record SSL/TLS certificates issued for domains and their subdomains, serving as a security measure to prevent fraudulent certificates. For reconnaissance, they offer a window into potentially overlooked subdomains.

The `crt.sh` website provides a searchable interface for CT logs. To efficiently extract subdomains using `crt.sh` within your terminal, you can use a command like this:

        bash
`curl -s "https://crt.sh/?q=%25.example.com&output=json" | jq -r '.[].name_value' | sed 's/\*\.//g' | sort -u`

This command fetches JSON-formatted data from `crt.sh` for `example.com` (the `%` is a wildcard), extracts domain names using `jq`, removes any wildcard prefixes (`*.`) with `sed`, and finally sorts and deduplicates the results.

Web Crawling
------------

Web crawling is the automated exploration of a website's structure. A web crawler, or spider, systematically navigates through web pages by following links, mimicking a user's browsing behavior. This process maps out the site's architecture and gathers valuable information embedded within the pages.

A crucial file that guides web crawlers is `robots.txt`. This file resides in a website's root directory and dictates which areas are off-limits for crawlers. Analyzing `robots.txt` can reveal hidden directories or sensitive areas that the website owner doesn't want to be indexed by search engines.

`Scrapy` is a powerful and efficient Python framework for large-scale web crawling and scraping projects. It provides a structured approach to defining crawling rules, extracting data, and handling various output formats.

Here's a basic Scrapy spider example to extract links from `example.com`:

        python
`import scrapy  class ExampleSpider(scrapy.Spider):     name = "example"     start_urls = ['http://example.com/']      def parse(self, response):         for link in response.css('a::attr(href)').getall():             if any(link.endswith(ext) for ext in self.interesting_extensions):                 yield {"file": link}             elif not link.startswith("#") and not link.startswith("mailto:"):                 yield response.follow(link, callback=self.parse)`

After running the Scrapy spider, you'll have a file containing scraped data (e.g., `example_data.json`). You can analyze these results using standard command-line tools. For instance, to extract all links:

        bash
`jq -r '.[] | select(.file != null) | .file' example_data.json | sort -u`

This command uses `jq` to extract links, `awk` to isolate file extensions, `sort` to order them, and `uniq -c` to count their occurrences. By scrutinizing the extracted data, you can identify patterns, anomalies, or sensitive files that might be of interest for further investigation.

Search Engine Discovery
-----------------------

Leveraging search engines for reconnaissance involves utilizing their vast indexes of web content to uncover information about your target. This passive technique, often referred to as Open Source Intelligence (OSINT) gathering, can yield valuable insights without directly interacting with the target's systems.

By employing advanced search operators and specialized queries known as "Google Dorks," you can pinpoint specific information buried within search results. Here's a table of some useful search operators for web reconnaissance:

Operator

Description

Example

`site:`

Restricts search results to a specific website.

`site:example.com "password reset"`

`inurl:`

Searches for a specific term in the URL of a page.

`inurl:admin login`

`filetype:`

Limits results to files of a specific type.

`filetype:pdf "confidential report"`

`intitle:`

Searches for a term within the title of a page.

`intitle:"index of" /backup`

`cache:`

Shows the cached version of a webpage.

`cache:example.com`

`"search term"`

Searches for the exact phrase within quotation marks.

`"internal error" site:example.com`

`OR`

Combines multiple search terms.

`inurl:admin OR inurl:login`

`-`

Excludes specific terms from search results.

`inurl:admin -intext:wordpress`

By creatively combining these operators and crafting targeted queries, you can uncover sensitive documents, exposed directories, login pages, and other valuable information that may aid in your reconnaissance efforts.

Web Archives
------------

Web archives are digital repositories that store snapshots of websites across time, providing a historical record of their evolution. Among these archives, the Wayback Machine is the most comprehensive and accessible resource for web reconnaissance.

The Wayback Machine, a project by the Internet Archive, has been archiving the web for over two decades, capturing billions of web pages from across the globe. This massive historical data collection can be an invaluable resource for security researchers and investigators.

Feature

Description

Use Case in Reconnaissance

`Historical Snapshots`

View past versions of websites, including pages, content, and design changes.

Identify past website content or functionality that is no longer available.

`Hidden Directories`

Explore directories and files that may have been removed or hidden from the current version of the website.

Discover sensitive information or backups that were inadvertently left accessible in previous versions.

`Content Changes`

Track changes in website content, including text, images, and links.

Identify patterns in content updates and assess the evolution of a website's security posture.

By leveraging the Wayback Machine, you can gain a historical perspective on your target's online presence, potentially revealing vulnerabilities that may have been overlooked in the current version of the website.

* * *

Download Cheatsheet

Close

Pwnbox Reset
------------

The current instance of Pwnbox will be terminated when switching to a new region. All progress you've made will be lost. Do you want to continue?

Continue

Cancel

Close