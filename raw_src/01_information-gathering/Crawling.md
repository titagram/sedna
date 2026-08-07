Crawling | Hack The Box Academy

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
    

Section 12 / 19

Crawling
========

`Crawling`, often called `spidering`, is the `automated process of systematically browsing the World Wide Web`. Similar to how a spider navigates its web, a web crawler follows links from one page to another, collecting information. These crawlers are essentially bots that use pre-defined algorithms to discover and index web pages, making them accessible through search engines or for other purposes like data analysis and web reconnaissance.

How Web Crawlers Work
---------------------

The basic operation of a web crawler is straightforward yet powerful. It starts with a seed URL, which is the initial web page to crawl. The crawler fetches this page, parses its content, and extracts all its links. It then adds these links to a queue and crawls them, repeating the process iteratively. Depending on its scope and configuration, the crawler can explore an entire website or even a vast portion of the web.

1.  `Homepage`: You start with the homepage containing `link1`, `link2`, and `link3`.
    
            txt
    `Homepage ├── link1 ├── link2 └── link3`
    
2.  `Visiting link1`: Visiting `link1` shows the homepage, `link2`, and also `link4` and `link5`.
    
            txt
    `link1 Page ├── Homepage ├── link2 ├── link4 └── link5`
    
3.  `Continuing the Crawl`: The crawler continues to follow these links systematically, gathering all accessible pages and their links.

This example illustrates how a web crawler discovers and collects information by systematically following links, distinguishing it from fuzzing which involves guessing potential links.

There are two primary types of crawling strategies.

### Breadth-First Crawling

![Flowchart showing a Seed URL leading to Page 1, which branches to Page 2 and Page 3. Page 2 connects to Page 4 and Page 5, while Page 3 connects to Page 6 and Page 7.](https://cdn.services-k8s.prod.aws.htb.systems/content/modules/144/ig_crawling_1.png)

`Breadth-first crawling` prioritizes exploring a website's width before going deep. It starts by crawling all the links on the seed page, then moves on to the links on those pages, and so on. This is useful for getting a broad overview of a website's structure and content.

### Depth-First Crawling

![Flowchart showing a Seed URL leading to Page 1, then to Page 2. Page 2 connects to Page 3, which branches to Page 4 and Page 5.](https://mermaid.ink/svg/pako:eNo9zz0PgjAQBuC_0twsg18LgwlfGyYG4uQ5VHoC0RZS2sEQ_rsnTezU98mlvXeGZlAEMbRWjp0oKzSTf4RQEylxrUo0gk9yu8iWxPaOhoxCk4goOok06I41XSELsGfIVsgDHBjyFYoAR4YivCEEGtiAJqtlr3iZ-fclgutIE0LMVyXtCwHNwnPSu6H-mAZiZz1twA6-7SB-yvfEyY9KOsp7ySX0X0n1brDn0HWtvHwB2SFOww)

In contrast, `depth-first crawling` prioritizes depth over breadth. It follows a single path of links as far as possible before backtracking and exploring other paths. This can be useful for finding specific content or reaching deep into a website's structure.

The choice of strategy depends on the specific goals of the crawling process.

Extracting Valuable Information
-------------------------------

Crawlers can extract a diverse array of data, each serving a specific purpose in the reconnaissance process:

*   `Links (Internal and External)`: These are the fundamental building blocks of the web, connecting pages within a website (`internal links`) and to other websites (`external links`). Crawlers meticulously collect these links, allowing you to map out a website's structure, discover hidden pages, and identify relationships with external resources.
*   `Comments`: Comments sections on blogs, forums, or other interactive pages can be a goldmine of information. Users often inadvertently reveal sensitive details, internal processes, or hints of vulnerabilities in their comments.
*   `Metadata`: Metadata refers to `data about data`. In the context of web pages, it includes information like page titles, descriptions, keywords, author names, and dates. This metadata can provide valuable context about a page's content, purpose, and relevance to your reconnaissance goals.
*   `Sensitive Files`: Web crawlers can be configured to actively search for sensitive files that might be inadvertently exposed on a website. This includes `backup files` (e.g., `.bak`, `.old`), `configuration files` (e.g., `web.config`, `settings.php`), `log files` (e.g., `error_log`, `access_log`), and other files containing passwords, `API keys`, or other confidential information. Carefully examining the extracted files, especially backup and configuration files, can reveal a trove of sensitive information, such as `database credentials`, `encryption keys`, or even source code snippets.

### The Importance of Context

Understanding the context surrounding the extracted data is paramount.

A single piece of information, like a comment mentioning a specific software version, might not seem significant on its own. However, when combined with other findings—such as an outdated version listed in metadata or a potentially vulnerable configuration file discovered through crawling—it can transform into a critical indicator of a potential vulnerability.

The true value of extracted data lies in connecting the dots and constructing a comprehensive picture of the target's digital landscape.

For instance, a list of extracted links might initially appear mundane. But upon closer examination, you notice a pattern: several URLs point to a directory named `/files/`. This triggers your curiosity, and you decide to manually visit the directory. To your surprise, you find that directory browsing is enabled, exposing a host of files, including backup archives, internal documents, and potentially sensitive data. This discovery wouldn't have been possible by merely looking at individual links in isolation; the contextual analysis led you to this critical finding.

Similarly, seemingly innocuous comments can gain significance when correlated with other discoveries. A comment mentioning a "file server" might not raise any red flags initially. However, when combined with the aforementioned discovery of the `/files/` directory, it reinforces the possibility that the file server is publicly accessible, potentially exposing sensitive information or confidential data.

Therefore, it's essential to approach data analysis holistically, considering the relationships between different data points and their potential implications for your reconnaissance goals.

Previous

Section 12 / 19

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