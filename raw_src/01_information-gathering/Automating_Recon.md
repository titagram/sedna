Automating Recon | Hack The Box Academy

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
    

Section 18 / 19

Automating Recon
================

* * *

While manual reconnaissance can be effective, it can also be time-consuming and prone to human error. Automating web reconnaissance tasks can significantly enhance efficiency and accuracy, allowing you to gather information at scale and identify potential vulnerabilities more rapidly.

Why Automate Reconnaissance?
----------------------------

Automation offers several key advantages for web reconnaissance:

*   `Efficiency`: Automated tools can perform repetitive tasks much faster than humans, freeing up valuable time for analysis and decision-making.
*   `Scalability`: Automation allows you to scale your reconnaissance efforts across a large number of targets or domains, uncovering a broader scope of information.
*   `Consistency`: Automated tools follow predefined rules and procedures, ensuring consistent and reproducible results and minimising the risk of human error.
*   `Comprehensive Coverage`: Automation can be programmed to perform a wide range of reconnaissance tasks, including DNS enumeration, subdomain discovery, web crawling, port scanning, and more, ensuring thorough coverage of potential attack vectors.
*   `Integration`: Many automation frameworks allow for easy integration with other tools and platforms, creating a seamless workflow from reconnaissance to vulnerability assessment and exploitation.

Reconnaissance Frameworks
-------------------------

These frameworks aim to provide a complete suite of tools for web reconnaissance:

*   [FinalRecon](https://github.com/thewhiteh4t/FinalRecon): A Python-based reconnaissance tool offering a range of modules for different tasks like SSL certificate checking, Whois information gathering, header analysis, and crawling. Its modular structure enables easy customisation for specific needs.
*   [Recon-ng](https://github.com/lanmaster53/recon-ng): A powerful framework written in Python that offers a modular structure with various modules for different reconnaissance tasks. It can perform DNS enumeration, subdomain discovery, port scanning, web crawling, and even exploit known vulnerabilities.
*   [theHarvester](https://github.com/laramies/theHarvester): Specifically designed for gathering email addresses, subdomains, hosts, employee names, open ports, and banners from different public sources like search engines, PGP key servers, and the SHODAN database. It is a command-line tool written in Python.
*   [SpiderFoot](https://github.com/smicallef/spiderfoot): An open-source intelligence automation tool that integrates with various data sources to collect information about a target, including IP addresses, domain names, email addresses, and social media profiles. It can perform DNS lookups, web crawling, port scanning, and more.
*   [OSINT Framework](https://osintframework.com/): A collection of various tools and resources for open-source intelligence gathering. It covers a wide range of information sources, including social media, search engines, public records, and more.

### FinalRecon

`FinalRecon` offers a wealth of recon information:

*   `Header Information`: Reveals server details, technologies used, and potential security misconfigurations.
*   `Whois Lookup`: Uncovers domain registration details, including registrant information and contact details.
*   `SSL Certificate Information`: Examines the SSL/TLS certificate for validity, issuer, and other relevant details.
*   `Crawler`:
    *   HTML, CSS, JavaScript: Extracts links, resources, and potential vulnerabilities from these files.
    *   Internal/External Links: Maps out the website's structure and identifies connections to other domains.
    *   Images, robots.txt, sitemap.xml: Gathers information about allowed/disallowed crawling paths and website structure.
    *   Links in JavaScript, Wayback Machine: Uncovers hidden links and historical website data.
*   `DNS Enumeration`: Queries over 40 DNS record types, including DMARC records for email security assessment.
*   `Subdomain Enumeration`: Leverages multiple data sources (crt.sh, AnubisDB, ThreatMiner, CertSpotter, Facebook API, VirusTotal API, Shodan API, BeVigil API) to discover subdomains.
*   `Directory Enumeration`: Supports custom wordlists and file extensions to uncover hidden directories and files.
*   `Wayback Machine`: Retrieves URLs from the last five years to analyse website changes and potential vulnerabilities.

Installation is quick and easy:

        shellsession
`titagram@htb[/htb]$ git clone https://github.com/thewhiteh4t/FinalRecon.git titagram@htb[/htb]$ cd FinalRecon titagram@htb[/htb]$ pip3 install -r requirements.txt titagram@htb[/htb]$ chmod +x ./finalrecon.py titagram@htb[/htb]$ ./finalrecon.py --help  usage: finalrecon.py [-h] [--url URL] [--headers] [--sslinfo] [--whois]                      [--crawl] [--dns] [--sub] [--dir] [--wayback] [--ps]                      [--full] [-nb] [-dt DT] [-pt PT] [-T T] [-w W] [-r] [-s]                      [-sp SP] [-d D] [-e E] [-o O] [-cd CD] [-k K]  FinalRecon - All in One Web Recon | v1.1.6  optional arguments:   -h, --help  show this help message and exit   --url URL   Target URL   --headers   Header Information   --sslinfo   SSL Certificate Information   --whois     Whois Lookup   --crawl     Crawl Target   --dns       DNS Enumeration   --sub       Sub-Domain Enumeration   --dir       Directory Search   --wayback   Wayback URLs   --ps        Fast Port Scan   --full      Full Recon  Extra Options:   -nb         Hide Banner   -dt DT      Number of threads for directory enum [ Default : 30 ]   -pt PT      Number of threads for port scan [ Default : 50 ]   -T T        Request Timeout [ Default : 30.0 ]   -w W        Path to Wordlist [ Default : wordlists/dirb_common.txt ]   -r          Allow Redirect [ Default : False ]   -s          Toggle SSL Verification [ Default : True ]   -sp SP      Specify SSL Port [ Default : 443 ]   -d D        Custom DNS Servers [ Default : 1.1.1.1 ]   -e E        File Extensions [ Example : txt, xml, php ]   -o O        Export Format [ Default : txt ]   -cd CD      Change export directory [ Default : ~/.local/share/finalrecon ]   -k K        Add API key [ Example : shodan@key ]`

To get started, you will first clone the `FinalRecon` repository from GitHub using `git clone https://github.com/thewhiteh4t/FinalRecon.git`. This will create a new directory named "FinalRecon" containing all the necessary files.

Next, navigate into the newly created directory with `cd FinalRecon`. Once inside, you will install the required Python dependencies using `pip3 install -r requirements.txt`. This ensures that `FinalRecon` has all the libraries and modules it needs to function correctly.

To ensure that the main script is executable, you will need to change the file permissions using `chmod +x ./finalrecon.py`. This allows you to run the script directly from your terminal.

Finally, you can verify that `FinalRecon` is installed correctly and get an overview of its available options by running `./finalrecon.py --help`. This will display a help message with details on how to use the tool, including the various modules and their respective options:

Option

Argument

Description

`-h`, `--help`

Show the help message and exit.

`--url`

URL

Specify the target URL.

`--headers`

Retrieve header information for the target URL.

`--sslinfo`

Get SSL certificate information for the target URL.

`--whois`

Perform a Whois lookup for the target domain.

`--crawl`

Crawl the target website.

`--dns`

Perform DNS enumeration on the target domain.

`--sub`

Enumerate subdomains for the target domain.

`--dir`

Search for directories on the target website.

`--wayback`

Retrieve Wayback URLs for the target.

`--ps`

Perform a fast port scan on the target.

`--full`

Perform a full reconnaissance scan on the target.

For instance, if we want `FinalRecon` to gather header information and perform a Whois lookup for `inlanefreight.com`, we would use the corresponding flags (`--headers` and `--whois`), so the command would be:

        shellsession
`titagram@htb[/htb]$ ./finalrecon.py --headers --whois --url http://inlanefreight.com   ______  __   __   __   ______   __ /\  ___\/\ \ /\ "-.\ \ /\  __ \ /\ \ \ \  __\\ \ \\ \ \-.  \\ \  __ \\ \ \____  \ \_\   \ \_\\ \_\\"\_\\ \_\ \_\\ \_____\   \/_/    \/_/ \/_/ \/_/ \/_/\/_/ \/_____/  ______   ______   ______   ______   __   __ /\  == \ /\  ___\ /\  ___\ /\  __ \ /\ "-.\ \ \ \  __< \ \  __\ \ \ \____\ \ \/\ \\ \ \-.  \  \ \_\ \_\\ \_____\\ \_____\\ \_____\\ \_\\"\_\   \/_/ /_/ \/_____/ \/_____/ \/_____/ \/_/ \/_/  [>] Created By   : thewhiteh4t  |---> Twitter   : https://twitter.com/thewhiteh4t  |---> Community : https://twc1rcle.com/ [>] Version      : 1.1.6  [+] Target : http://inlanefreight.com  [+] IP Address : 134.209.24.248  [!] Headers :  Date : Tue, 11 Jun 2024 10:08:00 GMT Server : Apache/2.4.41 (Ubuntu) Link : <https://www.inlanefreight.com/index.php/wp-json/>; rel="https://api.w.org/", <https://www.inlanefreight.com/index.php/wp-json/wp/v2/pages/7>; rel="alternate"; type="application/json", <https://www.inlanefreight.com/>; rel=shortlink Vary : Accept-Encoding Content-Encoding : gzip Content-Length : 5483 Keep-Alive : timeout=5, max=100 Connection : Keep-Alive Content-Type : text/html; charset=UTF-8  [!] Whois Lookup :      Domain Name: INLANEFREIGHT.COM    Registry Domain ID: 2420436757_DOMAIN_COM-VRSN    Registrar WHOIS Server: whois.registrar.amazon.com    Registrar URL: http://registrar.amazon.com    Updated Date: 2023-07-03T01:11:15Z    Creation Date: 2019-08-05T22:43:09Z    Registry Expiry Date: 2024-08-05T22:43:09Z    Registrar: Amazon Registrar, Inc.    Registrar IANA ID: 468    Registrar Abuse Contact Email: abuse@amazonaws.com    Registrar Abuse Contact Phone: +1.2024422253    Domain Status: clientDeleteProhibited https://icann.org/epp#clientDeleteProhibited    Domain Status: clientTransferProhibited https://icann.org/epp#clientTransferProhibited    Domain Status: clientUpdateProhibited https://icann.org/epp#clientUpdateProhibited    Name Server: NS-1303.AWSDNS-34.ORG    Name Server: NS-1580.AWSDNS-05.CO.UK    Name Server: NS-161.AWSDNS-20.COM    Name Server: NS-671.AWSDNS-19.NET    DNSSEC: unsigned    URL of the ICANN Whois Inaccuracy Complaint Form: https://www.icann.org/wicf/   [+] Completed in 0:00:00.257780  [+] Exported : /home/htb-ac-643601/.local/share/finalrecon/dumps/fr_inlanefreight.com_11-06-2024_11:07:59`

Previous

Section 18 / 19

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