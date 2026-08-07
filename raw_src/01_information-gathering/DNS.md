DNS | Hack The Box Academy

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
    

Section 4 / 19

DNS
===

* * *

The `Domain Name System` (`DNS`) acts as the internet's GPS, guiding your online journey from memorable landmarks (domain names) to precise numerical coordinates (IP addresses). Much like how GPS translates a destination name into latitude and longitude for navigation, DNS translates human-readable domain names (like `www.example.com`) into the numerical IP addresses (like `192.0.2.1`) that computers use to communicate.

Imagine navigating a city by memorizing the exact latitude and longitude of every location you want to visit. It would be incredibly cumbersome and inefficient. DNS eliminates this complexity by allowing us to use easy-to-remember domain names instead. When you type a domain name into your browser, DNS acts as your navigator, swiftly finding the corresponding IP address and directing your request to the correct destination on the internet.

Without DNS, navigating the online world would be akin to driving without a map or GPS – a frustrating and error-prone endeavour.

How DNS Works
-------------

Imagine you want to visit a website like `www.example.com`. You type this friendly domain name into your browser, but your computer doesn't understand words – it speaks the language of numbers, specifically IP addresses. So, how does your computer find the website's IP address? Enter DNS, the internet's trusty translator.

![Flowchart showing two main sections: User Database and Data Collection. Includes steps like 'Check User', 'Send to Source Database', 'Store Data', and 'Send to UI System'.](https://mermaid.ink/svg/pako:eNptkk1uwjAQha8y8rpcIItWkAAtUNQmlSrksDDxlEQQT-QfJIS4ex2nNG1arzx-n5-ex3NhBUlkEdtr0ZSwSnMFfhm36w425DTEVDfOou60do15XGJxMBCLosQtjEb3MLk8vcCMnJIP156ceA3WFIiYZ6ikgWSdwatDfQZLkKKh4wn1dnBngyZceuQxKYWFNS39jjtTWfyCvdsgb2t9c-wN4-CU_A7dy8mPjFOeYuG0qU4IK6KDa4bgLdjck9ZpZcC_20e7dWmYbRroGU-JLKxFjZCh7h88C_KCv62Sf9RFUJd87GxJurLCtsH-cssuUlfMu8blit2xGnUtKul_-NKKObMl1pizyG-l0Iec5erqOeEsZWdVsMhqh3dMk9uXLPoQR-Mr10hhMamE73L9fYqysqSfuwEKc3T9BOe0sj4)

1.  `Your Computer Asks for Directions (DNS Query)`: When you enter the domain name, your computer first checks its memory (cache) to see if it remembers the IP address from a previous visit. If not, it reaches out to a DNS resolver, usually provided by your internet service provider (ISP).
2.  `The DNS Resolver Checks its Map (Recursive Lookup)`: The resolver also has a cache, and if it doesn't find the IP address there, it starts a journey through the DNS hierarchy. It begins by asking a root name server, which is like the librarian of the internet.
3.  `Root Name Server Points the Way`: The root server doesn't know the exact address but knows who does – the Top-Level Domain (TLD) name server responsible for the domain's ending (e.g., .com, .org). It points the resolver in the right direction.
4.  `TLD Name Server Narrows It Down`: The TLD name server is like a regional map. It knows which authoritative name server is responsible for the specific domain you're looking for (e.g., `example.com`) and sends the resolver there.
5.  `Authoritative Name Server Delivers the Address`: The authoritative name server is the final stop. It's like the street address of the website you want. It holds the correct IP address and sends it back to the resolver.
6.  `The DNS Resolver Returns the Information`: The resolver receives the IP address and gives it to your computer. It also remembers it for a while (caches it), in case you want to revisit the website soon.
7.  `Your Computer Connects`: Now that your computer knows the IP address, it can connect directly to the web server hosting the website, and you can start browsing.

### The Hosts File

The `hosts` file is a simple text file used to map hostnames to IP addresses, providing a manual method of domain name resolution that bypasses the DNS process. While DNS automates the translation of domain names to IP addresses, the `hosts` file allows for direct, local overrides. This can be particularly useful for development, troubleshooting, or blocking websites.

The `hosts` file is located in `C:\Windows\System32\drivers\etc\hosts` on Windows and in `/etc/hosts` on Linux and MacOS. Each line in the file follows the format:

        txt
`<IP Address>    <Hostname> [<Alias> ...]`

For example:

        txt
`127.0.0.1       localhost 192.168.1.10    devserver.local`

To edit the `hosts` file, open it with a text editor using administrative/root privileges. Add new entries as needed, and then save the file. The changes take effect immediately without requiring a system restart.

Common uses include redirecting a domain to a local server for development:

        txt
`127.0.0.1       myapp.local`

testing connectivity by specifying an IP address:

        txt
`192.168.1.20    testserver.local`

or blocking unwanted websites by redirecting their domains to a non-existent IP address:

        txt
`0.0.0.0       unwanted-site.com`

### It's Like a Relay Race

Think of the DNS process as a relay race. Your computer starts with the domain name and passes it along to the resolver. The resolver then passes the request to the root server, the TLD server, and finally, the authoritative server, each one getting closer to the destination. Once the IP address is found, it's relayed back down the chain to your computer, allowing you to access the website.

### Key DNS Concepts

In the `Domain Name System` (`DNS`), a `zone` is a distinct part of the domain namespace that a specific entity or administrator manages. Think of it as a virtual container for a set of domain names. For example, `example.com` and all its subdomains (like `mail.example.com` or `blog.example.com`) would typically belong to the same DNS zone.

The zone file, a text file residing on a DNS server, defines the resource records (discussed below) within this zone, providing crucial information for translating domain names into IP addresses.

To illustrate, here's a simplified example of what a zone file, for `example.com` might look like:

        dns-zone
`$TTL 3600 ; Default Time-To-Live (1 hour) @       IN SOA   ns1.example.com. admin.example.com. (                 2024060401 ; Serial number (YYYYMMDDNN)                 3600       ; Refresh interval                 900        ; Retry interval                 604800     ; Expire time                 86400 )    ; Minimum TTL  @       IN NS    ns1.example.com. @       IN NS    ns2.example.com. @       IN MX 10 mail.example.com. www     IN A     192.0.2.1 mail    IN A     198.51.100.1 ftp     IN CNAME www.example.com.`

This file defines the authoritative name servers (`NS` records), mail server (`MX` record), and IP addresses (`A` records) for various hosts within the `example.com` domain.

DNS servers store various resource records, each serving a specific purpose in the domain name resolution process. Let's explore some of the most common DNS concepts:

DNS Concept

Description

Example

`Domain Name`

A human-readable label for a website or other internet resource.

`www.example.com`

`IP Address`

A unique numerical identifier assigned to each device connected to the internet.

`192.0.2.1`

`DNS Resolver`

A server that translates domain names into IP addresses.

Your ISP's DNS server or public resolvers like Google DNS (`8.8.8.8`)

`Root Name Server`

The top-level servers in the DNS hierarchy.

There are 13 root servers worldwide, named A-M: `a.root-servers.net`

`TLD Name Server`

Servers responsible for specific top-level domains (e.g., .com, .org).

[Verisign](https://en.wikipedia.org/wiki/Verisign) for `.com`, [PIR](https://en.wikipedia.org/wiki/Public_Interest_Registry) for `.org`

`Authoritative Name Server`

The server that holds the actual IP address for a domain.

Often managed by hosting providers or domain registrars.

`DNS Record Types`

Different types of information stored in DNS.

A, AAAA, CNAME, MX, NS, TXT, etc.

Now that we've explored the fundamental concepts of DNS, let's dive deeper into the building blocks of DNS information – the various record types. These records store different types of data associated with domain names, each serving a specific purpose:

Record Type

Full Name

Description

Zone File Example

`A`

Address Record

Maps a hostname to its IPv4 address.

`www.example.com.` IN A `192.0.2.1`

`AAAA`

IPv6 Address Record

Maps a hostname to its IPv6 address.

`www.example.com.` IN AAAA `2001:db8:85a3::8a2e:370:7334`

`CNAME`

Canonical Name Record

Creates an alias for a hostname, pointing it to another hostname.

`blog.example.com.` IN CNAME `webserver.example.net.`

`MX`

Mail Exchange Record

Specifies the mail server(s) responsible for handling email for the domain.

`example.com.` IN MX 10 `mail.example.com.`

`NS`

Name Server Record

Delegates a DNS zone to a specific authoritative name server.

`example.com.` IN NS `ns1.example.com.`

`TXT`

Text Record

Stores arbitrary text information, often used for domain verification or security policies.

`example.com.` IN TXT `"v=spf1 mx -all"` (SPF record)

`SOA`

Start of Authority Record

Specifies administrative information about a DNS zone, including the primary name server, responsible person's email, and other parameters.

`example.com.` IN SOA `ns1.example.com. admin.example.com. 2024060301 10800 3600 604800 86400`

`SRV`

Service Record

Defines the hostname and port number for specific services.

`_sip._udp.example.com.` IN SRV 10 5 5060 `sipserver.example.com.`

`PTR`

Pointer Record

Used for reverse DNS lookups, mapping an IP address to a hostname.

`1.2.0.192.in-addr.arpa.` IN PTR `www.example.com.`

The "`IN`" in the examples stands for "Internet." It's a class field in DNS records that specifies the protocol family. In most cases, you'll see "`IN`" used, as it denotes the Internet protocol suite (IP) used for most domain names. Other class values exist (e.g., `CH` for Chaosnet, `HS` for Hesiod) but are rarely used in modern DNS configurations.

In essence, "`IN`" is simply a convention that indicates that the record applies to the standard internet protocols we use today. While it might seem like an extra detail, understanding its meaning provides a deeper understanding of DNS record structure.

Why DNS Matters for Web Recon
-----------------------------

DNS is not merely a technical protocol for translating domain names; it's a critical component of a target's infrastructure that can be leveraged to uncover vulnerabilities and gain access during a penetration test:

*   `Uncovering Assets`: DNS records can reveal a wealth of information, including subdomains, mail servers, and name server records. For instance, a `CNAME` record pointing to an outdated server (`dev.example.com` CNAME `oldserver.example.net`) could lead to a vulnerable system.
*   `Mapping the Network Infrastructure`: You can create a comprehensive map of the target's network infrastructure by analysing DNS data. For example, identifying the name servers (`NS` records) for a domain can reveal the hosting provider used, while an `A` record for `loadbalancer.example.com` can pinpoint a load balancer. This helps you understand how different systems are connected, identify traffic flow, and pinpoint potential choke points or weaknesses that could be exploited during a penetration test.
*   `Monitoring for Changes`: Continuously monitoring DNS records can reveal changes in the target's infrastructure over time. For example, the sudden appearance of a new subdomain (`vpn.example.com`) might indicate a new entry point into the network, while a `TXT` record containing a value like `_1password=...` strongly suggests the organization is using 1Password, which could be leveraged for social engineering attacks or targeted phishing campaigns.

Previous

Section 4 / 19

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