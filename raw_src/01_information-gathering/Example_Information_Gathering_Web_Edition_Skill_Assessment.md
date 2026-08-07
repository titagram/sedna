Information Gathering — Web Edition Skill Assessment | by Saddanr | MediumMediumInformation Gathering — Web Edition Skill Assessment | by Saddanr | Medium 

[Sitemap](/sitemap/sitemap.xml)

[Open in app](https://play.google.com/store/apps/details?id=com.medium.reader&referrer=utm_source%3DmobileNavBar&source=---top_nav_layout_nav-----------------------------------------)

Sidebar menu

[Medium Logo](/?source=---top_nav_layout_nav-----------------------------------------)

[

Write

](https://medium.com/new-story?source=---top_nav_layout_nav-----------------------------------------)

[

Search

](/search?source=---top_nav_layout_nav-----------------------------------------)

[

Notifications

2



](/me/notifications?source=---top_nav_layout_nav-----------------------------------------)

![Gabriele Tita](https://miro.medium.com/v2/resize:fill:64:64/0*u1sfoZCh7vIIQATS)

Sidebar menu

[Medium Logo](/?source=---sidebar_menu-----------------------------------------)

[Home](/?source=---sidebar_menu-----------------------------------------)

[Library](/me/lists?source=---sidebar_menu-----------------------------------------)

[Profile](/@g.titagram?source=---sidebar_menu-----------------------------------------)

[Stories](/me/stories?source=---sidebar_menu-----------------------------------------)

[Stats](/me/stats?source=---sidebar_menu-----------------------------------------)

[Following](/me/following-feed/writers?source=---sidebar_menu-----------------------------------------)

[](/me/following?source=---sidebar_menu-----------------------------------------)

Writers and publications

[

![The Medium Blog](https://miro.medium.com/v2/resize:fill:32:32/1*7eq6Xl7nRYU77U7IPYvoDg.jpeg)

The Medium Blog

](/me/following-feed/publications/15f753907972?source=---sidebar_menu_following------------------------------following_badge-----------)

[

![Medium Staff](https://miro.medium.com/v2/resize:fill:32:32/1*8E6Laeaz-zMfU_rkpZUyKw.png)

Medium Staff

](/me/following-feed/writers/a32c340ea342?source=---sidebar_menu_following------------------------------following_badge-----------)

[

![Aurora Zarcone](https://miro.medium.com/v2/resize:fill:32:32/1*XcOmrB_8EMeOSf5SuJqChQ@2x.jpeg)

Aurora Zarcone

](/me/following-feed/writers/d2aa470a251d?source=---sidebar_menu_following------------------------------following_badge-----------)

[

![dax](https://miro.medium.com/v2/resize:fill:32:32/1*qQVChnV7LJsVUoyiCZYwfg.png)

dax](/me/following-feed/writers/38844d2c6700?source=---sidebar_menu_following-----------------------------------------)

Find topics, writers and publications to follow.

[See suggestions](/me/following/suggestions?source=---sidebar_menu_following-----------------------------------------)

Welcome Offer

Access to everything. Now 30% off.[

Upgrade now

Upgrade now

](/plans?source=membership_discount_banner---post_top_nav_upsell-----------------------------------------)

1.  [Information Gathering — Web Edition Skill Assessment](/?source=post_page-----0f6bec83a6a1---------------------------------------#b2dc "Information Gathering — Web Edition Skill Assessment")
2.  [1 — What is the IANA ID of the registrar of the inlanefreight.com domain?](/?source=post_page-----0f6bec83a6a1---------------------------------------#fcb5 "1 — What is the IANA ID of the registrar of the inlanefreight.com domain?")
3.  [2 — What HTTP server software is powering inlanefreight.htb on the target system?](/?source=post_page-----0f6bec83a6a1---------------------------------------#0b85 "2 — What HTTP server software is powering inlanefreight.htb on the target system?")
4.  [3 — What is the API key in the hidden admin directory you discovered?](/?source=post_page-----0f6bec83a6a1---------------------------------------#c79e "3 — What is the API key in the hidden admin directory you discovered?")
5.  [4 — After crawling the inlanefreight.htb domain, what email address did you find?](/?source=post_page-----0f6bec83a6a1---------------------------------------#6c0c "4 — After crawling the inlanefreight.htb domain, what email address did you find?")
6.  [5 — What is the API key the inlanefreight.htb developers will be changing to?](/?source=post_page-----0f6bec83a6a1---------------------------------------#8ea3 "5 — What is the API key the inlanefreight.htb developers will be changing to?")
7.  [Final notes](/?source=post_page-----0f6bec83a6a1---------------------------------------#9ac8 "Final notes")

[

![Saddanr](https://miro.medium.com/v2/resize:fill:80:80/1*zZm99xvXaGa2F-YPFXculg.png)



](/@isaddanr?source=post_page---post_author_sidebar--0f6bec83a6a1-----------------324644056027----------------------)

Saddanr
-------

Offensive security & stuffs

Follow writer

Htb Academy

Htb

Htb Writeup

Htb Walkthrough

Information Gathering — Web Edition Skill Assessment
====================================================

[

![Saddanr](https://miro.medium.com/v2/resize:fill:64:64/1*zZm99xvXaGa2F-YPFXculg.png)





](/@isaddanr?source=post_page---byline--0f6bec83a6a1---------------------------------------)

[Saddanr](/@isaddanr?source=post_page---byline--0f6bec83a6a1---------------------------------------)

Follow

6 min read

·

Oct 6, 2025

110

5

[

Listen









](/plans?dimension=post_audio_button&postId=0f6bec83a6a1&source=upgrade_membership---post_audio_button-----------------------------------------)

Share

More

Information Gathering — Web Edition Skill Assessment
----------------------------------------------------

This writeup documents my approach to the **HTB Information Gathering — Web Edition** skill assessment.

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*8Ozo13su0Bpb7uAQt41zFg.jpeg)

— — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — —

To complete the skills assessment, answer the questions below. You will need to apply a variety of skills learned in this module, including:

*   Using `whois`
*   Analysing `robots.txt`
*   Performing subdomain bruteforcing
*   Crawling and analysing results

Demonstrate your proficiency by effectively utilizing these techniques. Remember to add subdomains to your `hosts` file as you discover them.

Target(s): 94.237.57.211:34677

vHosts needed for these questions:

*   `inlanefreight.htb`

— — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — — —

I need to answer 5 questions that test my understanding of web information gathering in this module. Let’s dive in.

1 — What is the IANA ID of the registrar of the inlanefreight.com domain?
-------------------------------------------------------------------------

This asks which registrar manages `inlanefreight.com` and what their official IANA ID is. Easy — run `whois` on the domain.

The `whois` command shows domain registration information (owner, registrar, registration dates, name servers, etc.):

whois inlanefreight.com

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*YWnJnrRrZdzu2S1AiqZ0ZA.png)

From the `whois` output we can see the registrar is **Amazon Registrar, Inc.** and the IANA ID is **468**.

2 — What HTTP server software is powering `inlanefreight.htb` on the target system?
-----------------------------------------------------------------------------------

We need the name of the HTTP server software (not the version). The quickest way is to fetch response headers with `curl`:

curl -i inlanefreight.htb:34677  
curl -i inlanefreight.htb

![](https://miro.medium.com/v2/resize:fit:1060/1*YHrVBcWVVkz2xp2VX1URnw.png)

At first the hostname didn’t resolve on my machine (because `inlanefreight.htb` isn’t public DNS-resolvable), so I added it to `/etc/hosts` and pointed it at the target IP:

sudo nano /etc/hosts  
\# add:  
\# 94.237.57.211 inlanefreight.htb

![](https://miro.medium.com/v2/resize:fit:1000/1*Z_pMR35vvn1Ij-LshM-euw.png)

After adding the hosts entry I retried:

curl -i inlanefreight.htb:34677

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*KGdqi_BVbl1HLpmupXZ8qA.png)

The response headers indicate the web server is **nginx**.

3 — What is the API key in the hidden admin directory you discovered?
---------------------------------------------------------------------

The prompt mentions an admin directory, so I looked for it.

First I checked `robots.txt` and `sitemap.xml`:

curl -i inlanefreight.htb:34677/robots.txt  
curl -i inlanefreight.htb:34677/sitemap.xml

![](https://miro.medium.com/v2/resize:fit:1206/1*mpMddMUbzKStAZfGbHp-wQ.png)

No useful results. I then tried directory bruteforcing with `ffuf`:

ffuf -u http://inlanefreight.htb:34677/FUZZ -w /usr/share/seclists/Discovery/Web-Content/common.txt

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*9SS6FSpsmC_ppl_H4iU3QQ.png)

No luck. I expanded to a larger wordlist:

ffuf -u http://inlanefreight.htb:34677/FUZZ -w /usr/share/seclists/Discovery/Web-Content/raft-large-directories.txt

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*v9TRAJVoOJUo3vtjhck98w.png)

Also no luck.

After thinking it over, I suspected `inlanefreight.htb` might have subdomains we haven’t discovered yet, so I checked whether the server is running a DNS service:

nmap -p 53 -sU 94.237.57.211

The scan shows no DNS service on that host. That means, under my current assumption, if subdomains exist they are inaccessible and cannot be brute-forced through this server because there is no DNS server on the target to provide information about internal names.

The server runs nginx, which often serves multiple sites from a single IP, so vhost probing could still reveal additional pages.

[](/write?source=promotion_paragraph---post_body_banner_jsw_scribble--0f6bec83a6a1---------------------------------------)

I bruteforced vhosts with `gobuster`:

gobuster vhost -u http://inlanefreight.htb:34677 -w /usr/share/seclists/Discovery/DNS/fierce-hostlist.txt -t 100 --append-domain

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*XXeBJPCmJsZrWtc9ZDE1uA.png)

That produced nothing; switching to a larger list:

gobuster vhost -u http://inlanefreight.htb:34677 -w /usr/share/seclists/Discovery/DNS/subdomains-top1million-110000.txt -t 500 --append-domain

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*yOk0HpLIbGUesrZGdPefBg.png)

This found a vhost: `web1337.inlanefreight.htb:34677`.

As before, `web1337.inlanefreight.htb` didn’t resolve, so I added it to `/etc/hosts`:

sudo nano /etc/hosts  
\# add:  
\# 94.237.57.211 web1337.inlanefreight.htb

![](https://miro.medium.com/v2/resize:fit:1002/1*eqZu7-kNhkhWBzVTeWurJw.png)

Then I fetched headers:

curl -i web1337.inlanefreight.htb:34677

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*ckbTt4Cj-EaewDg3s_QEvA.png)

Next I checked `robots.txt` and `sitemap.xml` on that vhost:

curl -i web1337.inlanefreight.htb:34677/robots.txt  
curl -i web1337.inlanefreight.htb:34677/sitemap.xml

![](https://miro.medium.com/v2/resize:fit:1212/1*N7shXm7kEEzSqbFtS6Wb4w.png)

Good find: `robots.txt` revealed an `/admin_h1dd3n` directory. I fetched that path:

curl -i web1337.inlanefreight.htb:34677/admin\_h1dd3n/

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*EZ9bELcydCDdzRPUV51cvA.png)

The response contained the API key.

**Answer (from the admin directory):** the API key is present in `/admin_h1dd3n/` (value shown in screenshot).

4 — After crawling the `inlanefreight.htb` domain, what email address did you find?
-----------------------------------------------------------------------------------

The question implies that a web crawl will surface an email address. I used ReconSpider to crawl the vhosts.

First I tried crawling the root vhost:

pip3 install scrapy  
wget -O ReconSpider.zip https://academy.hackthebox.com/storage/modules/144/ReconSpider.v1.2.zip  
unzip ReconSpider.zip  
python3 ReconSpider.py http://inlanefreight.htb:34677  
cat results.json

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1170/1*6VZ8UYvKRPHsRsK7o3PTYQ.png)

No results. Then I crawled `web1337.inlanefreight.htb`:

python3 ReconSpider.py http://web1337.inlanefreight.htb:34677  
cat results.json

![](https://miro.medium.com/v2/resize:fit:790/1*zETVZ_ckhB7Sl-YJMPsgKg.png)

Still no luck — at this point I was stuck, until I had a revelation that a sub-vhost can itself have sub-vhosts! 😀. So I tried brute-forcing sub-vhosts for `web1337.inlanefreight.htb:34677`

gobuster vhost -u http://web1337.inlanefreight.htb:34677 -w /usr/share/seclists/Discovery/DNS/subdomains-top1million-110000.txt -t 500 --append-domain

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*_nBy5L8Hkkg1gL_D_QKB7Q.png)

That revealed `dev.web1337.inlanefreight.htb:34677`. I added it to `/etc/hosts`:

sudo nano /etc/hosts  
\# add:  
\# 94.237.57.211 dev.web1337.inlanefreight.htb

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*ZqgqIEmKfS3b5sSxKE5t5g.png)

I confirmed the host:

curl -i dev.web1337.inlanefreight.htb:34677

Press enter or click to view image in full size

![](https://miro.medium.com/v2/resize:fit:1400/1*gyomk1N4gk-EBZW8dIYtPQ.png)

Then I ran ReconSpider on the dev vhost:

python3 ReconSpider.py http://dev.web1337.inlanefreight.htb:34677  
cat results.json

The crawl returned an email address and other artifacts. `results.json` included:

cat results.json   
{  
    "emails": \[  
        "1337testing@inlanefreight.htb"  
    \],  
    "links": \[  
        "http://dev.web1337.inlanefreight.htb:34677/index-105.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-224.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-24.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-166.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-459.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-862.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-431.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-687.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-332.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-895.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-379.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-817.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-513.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-728.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-291.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-660.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-458.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-555.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-567.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-785.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-334.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-733.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-933.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-254.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-947.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-134.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-350.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-408.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-574.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-342.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-755.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-203.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-631.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-525.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-202.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-226.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-626.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-247.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-948.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-949.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-918.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-384.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-989.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-988.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-364.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-909.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-964.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-329.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-939.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-635.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-220.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-77.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-165.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-204.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-385.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-465.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-531.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-815.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-504.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-769.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-795.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-463.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-1000.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-403.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-326.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-798.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-615.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-944.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-561.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-760.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-737.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-553.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-888.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-727.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-335.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-300.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-292.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-938.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-641.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-248.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-789.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-80.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-714.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-748.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-734.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-472.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-925.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-807.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-799.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-189.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-437.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-643.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-585.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-577.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-244.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-581.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-977.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-302.html",  
        "http://dev.web1337.inlanefreight.htb:34677/index-114.html"  
    \],  
    "external\_files": \[\],  
    "js\_files": \[\],  
    "form\_fields": \[\],  
    "images": \[\],  
    "videos": \[\],  
    "audio": \[\],  
    "comments": \[  
        "<!-- Remember to change the API key to ba988b835be4aa97d068941dc852ff33 -->"  
    \]

**Answer:** `1337testing@inlanefreight.htb`

5 — What is the API key the `inlanefreight.htb` developers will be changing to?
-------------------------------------------------------------------------------

We already found this in the earlier admin comment. The developer comment in the crawl shows the new API key:

<!-- Remember to change the API key to ba988b835be4aa97d068941dc852ff33 -->

**Answer:** `ba988b835be4aa97d068941dc852ff33`

Final notes
-----------

This box feels more challenging than it actually is— partly because some question wording is a bit misleading — but it forced me to try several approaches and reinforced what I’ve learned .

Htb Academy

Htb

Htb Writeup

Htb Walkthrough

110

110

5

[

![Saddanr](https://miro.medium.com/v2/resize:fill:96:96/1*zZm99xvXaGa2F-YPFXculg.png)



](/@isaddanr?source=post_page---post_author_info--0f6bec83a6a1---------------------------------------)

[

![Saddanr](https://miro.medium.com/v2/resize:fill:128:128/1*zZm99xvXaGa2F-YPFXculg.png)



](/@isaddanr?source=post_page---post_author_info--0f6bec83a6a1---------------------------------------)

Follow

[

Written by Saddanr
------------------

](/@isaddanr?source=post_page---post_author_info--0f6bec83a6a1---------------------------------------)

[50 followers](/@isaddanr/followers?source=post_page---post_author_info--0f6bec83a6a1---------------------------------------)

·[3 following](/@isaddanr/following?source=post_page---post_author_info--0f6bec83a6a1---------------------------------------)

Offensive security & stuffs

Follow

Responses (5)
-------------

[](https://policy.medium.com/medium-rules-30e5502c4eb4?source=post_page---post_responses--0f6bec83a6a1---------------------------------------)

![Gabriele Tita](https://miro.medium.com/v2/resize:fill:64:64/0*u1sfoZCh7vIIQATS)

Gabriele Tita

What are your thoughts?﻿

Cancel

Respond

[

![zele :3](https://miro.medium.com/v2/resize:fill:64:64/1*d2bFyEBSvnzBZS5szqBIHQ.jpeg)



](/@zirti?source=post_page---post_responses--0f6bec83a6a1----0-----------------------------------)

[

zele :3



](/@zirti?source=post_page---post_responses--0f6bec83a6a1----0-----------------------------------)

[

6 days ago

](/@zirti/holaaa-te-amo-muchas-gracias-8e1f69adda52?source=post_page---post_responses--0f6bec83a6a1----0-----------------------------------)

HOLAAA TE AMO MUCHAS GRACIAS 😭😭😭

I WAS ABOUT TO CRASH OUT WITH FLAG4

that chained vhost fuzz was so smart omg thank youu

Reply

[

![Lakshan roshana](https://miro.medium.com/v2/resize:fill:64:64/1*t5DyO317d8yJIGSvRquoRg.jpeg)



](/@lakshanroshana2?source=post_page---post_responses--0f6bec83a6a1----1-----------------------------------)

[

Lakshan roshana



](/@lakshanroshana2?source=post_page---post_responses--0f6bec83a6a1----1-----------------------------------)

[

May 31

](/@lakshanroshana2/thanks-for-the-explanation-this-is-really-helpful-for-me-d20a1a9f643e?source=post_page---post_responses--0f6bec83a6a1----1-----------------------------------)

thanks for the explanation, this is really helpful for me

Reply

[

![Shantellsas](https://miro.medium.com/v2/resize:fill:64:64/0*xLMxz4i-yhSnKJKY)



](/@shantell26sas?source=post_page---post_responses--0f6bec83a6a1----2-----------------------------------)

[

Shantellsas



](/@shantell26sas?source=post_page---post_responses--0f6bec83a6a1----2-----------------------------------)

[

May 19

](/@shantell26sas/thanks-this-helped-me-91ac978083f8?source=post_page---post_responses--0f6bec83a6a1----2-----------------------------------)

thanks this helped me

Reply

See all responses

More from Saddanr
-----------------

![HTB Password Attacks — All Questions and Answers Part 3 (Pass the Hash, Pass the Ticket and Pass…](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*5KlhPUlNiSPQH8oaS_-f8g.jpeg)

[

![Saddanr](https://miro.medium.com/v2/resize:fill:40:40/1*zZm99xvXaGa2F-YPFXculg.png)



](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1----0---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

Saddanr

](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1----0---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

·

Feb 25

[

HTB Password Attacks — All Questions and Answers Part 3 (Pass the Hash, Pass the Ticket and Pass…
-------------------------------------------------------------------------------------------------

### This writeup documents my approach to the HTB Password attacks Questions and answers.



](/@isaddanr/htb-password-attacks-all-questions-and-answers-part-3-pass-the-hash-pass-the-ticket-and-pass-090e37b46255?source=post_page---author_recirc--0f6bec83a6a1----0---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

A clap icon13

A response icon2







](/@isaddanr/htb-password-attacks-all-questions-and-answers-part-3-pass-the-hash-pass-the-ticket-and-pass-090e37b46255?source=post_page---author_recirc--0f6bec83a6a1----0---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

![HTB Password Attacks — All Questions and Answers Part 2 (Extracting Passwords from Windows Systems…](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*5KlhPUlNiSPQH8oaS_-f8g.jpeg)

[

![Saddanr](https://miro.medium.com/v2/resize:fill:40:40/1*zZm99xvXaGa2F-YPFXculg.png)



](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1----1---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

Saddanr

](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1----1---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

·

Nov 5, 2025

[

HTB Password Attacks — All Questions and Answers Part 2 (Extracting Passwords from Windows Systems…
---------------------------------------------------------------------------------------------------

### This writeup documents my approach to the HTB Password attacks Questions and answers.



](/@isaddanr/htb-password-attacks-all-questions-and-answers-part-2-extracting-passwords-from-windows-systems-fa7a7abf3bea?source=post_page---author_recirc--0f6bec83a6a1----1---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

A clap icon106

A response icon3







](/@isaddanr/htb-password-attacks-all-questions-and-answers-part-2-extracting-passwords-from-windows-systems-fa7a7abf3bea?source=post_page---author_recirc--0f6bec83a6a1----1---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

![HTB Password Attacks — All Questions and Answers Part 1 (Password Cracking Techniques & Remote…](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*5KlhPUlNiSPQH8oaS_-f8g.jpeg)

[

![Saddanr](https://miro.medium.com/v2/resize:fill:40:40/1*zZm99xvXaGa2F-YPFXculg.png)



](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1----2---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

Saddanr

](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1----2---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

·

Nov 3, 2025

[

HTB Password Attacks — All Questions and Answers Part 1 (Password Cracking Techniques & Remote…
-----------------------------------------------------------------------------------------------

### This writeup documents my approach to the HTB Password attacks Questions and answers.



](/@isaddanr/htb-password-attacks-all-questions-and-answers-part-1-password-cracking-techniques-remote-eb7480ba6db7?source=post_page---author_recirc--0f6bec83a6a1----2---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

A clap icon7







](/@isaddanr/htb-password-attacks-all-questions-and-answers-part-1-password-cracking-techniques-remote-eb7480ba6db7?source=post_page---author_recirc--0f6bec83a6a1----2---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

![Windows File Transfer Methods — Hands on Section](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*U4kPxjIXZjiZAy3Jfgb_yg.jpeg)

[

![Saddanr](https://miro.medium.com/v2/resize:fill:40:40/1*zZm99xvXaGa2F-YPFXculg.png)



](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1----3---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

Saddanr

](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1----3---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

·

Oct 12, 2025

[

Windows File Transfer Methods — Hands on Section
------------------------------------------------

### This writeup documents the hands-on section of the Windows File Transfer Methods module.



](/@isaddanr/windows-file-transfer-methods-hands-on-section-1ac616cf4ec6?source=post_page---author_recirc--0f6bec83a6a1----3---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

A clap icon10

A response icon2







](/@isaddanr/windows-file-transfer-methods-hands-on-section-1ac616cf4ec6?source=post_page---author_recirc--0f6bec83a6a1----3---------------------41fe7376_4817_40b4_a8e0_ea1230e08e60--------------)

[

See all from Saddanr

](/@isaddanr?source=post_page---author_recirc--0f6bec83a6a1---------------------------------------)

Recommended from Medium
-----------------------

![TryHackMe: OWASP Top 10 2025: Application Design Flaws](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*w28p5M1YJ_NpeoBAI0turA.png)

[

![Sudoroot](https://miro.medium.com/v2/resize:fill:40:40/0*cGQG1l6eZBOY5zh8)



](/@sudoroot523?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

Sudoroot

](/@sudoroot523?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

·

Apr 5

[

TryHackMe: OWASP Top 10 2025: Application Design Flaws
------------------------------------------------------

### Room: https://tryhackme.com/room/owasptopten2025two



](/@sudoroot523/tryhackme-owasp-top-10-2025-application-design-flaws-b379d9adb871?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

A clap icon3

A response icon2







](/@sudoroot523/tryhackme-owasp-top-10-2025-application-design-flaws-b379d9adb871?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

![The Night Claude Found a Critical IDOR and Deleted the Test Account](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*H_bO_SgIENx1T6H7ZjXb2w.png)

[

![Abhishek meena](https://miro.medium.com/v2/resize:fill:40:40/1*g4tYjgpvB52xwZPNMcvefg.png)



](/@Aacle?source=post_page---read_next_recirc--0f6bec83a6a1----1---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

Abhishek meena

](/@Aacle?source=post_page---read_next_recirc--0f6bec83a6a1----1---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

·

Jul 17

[

The Night Claude Found a Critical IDOR and Deleted the Test Account
-------------------------------------------------------------------

### The Claude Code Bug Bounty Run · Part 2 of 2 — Speed pays. Unsupervised write access bites back.



](/@Aacle/the-night-claude-found-a-critical-idor-and-deleted-the-test-account-6a685cc205d3?source=post_page---read_next_recirc--0f6bec83a6a1----1---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

A clap icon18

Repost icon2







](/@Aacle/the-night-claude-found-a-critical-idor-and-deleted-the-test-account-6a685cc205d3?source=post_page---read_next_recirc--0f6bec83a6a1----1---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

![How Much Can You Really Earn From Bug Bounty? A Realistic Look](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*j1GcAyLOMOEGEYv687jpmg.png)

[

![InfoSec Write-ups](https://miro.medium.com/v2/resize:fill:40:40/1*SWJxYWGZzgmBP1D0Qg_3zQ.png)



](https://medium.com/bugbountywriteup?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

In

[

InfoSec Write-ups

](https://medium.com/bugbountywriteup?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

by

[

Ismail Tasdelen

](/@ismailtasdelen?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

·

Jun 20

[

How Much Can You Really Earn From Bug Bounty? A Realistic Look
--------------------------------------------------------------

### Five years of part-time hunting, 750+ vulnerabilities, and roughly $50,000 later, here’s the honest version nobody puts in their LinkedIn…



](/bugbountywriteup/how-much-can-you-really-earn-from-bug-bounty-a-realistic-look-c3f5ac33aa1c?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

A clap icon208

A response icon6

Repost icon1







](/bugbountywriteup/how-much-can-you-really-earn-from-bug-bounty-a-realistic-look-c3f5ac33aa1c?source=post_page---read_next_recirc--0f6bec83a6a1----0---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

![Penetration Testing in Nutshell](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*fmuJSQYVZSXltwDZH5DI0Q.png)

[

![Abdelhamid Houari](https://miro.medium.com/v2/resize:fill:40:40/1*hYiZoMPrPdeCmmHrYbrghw.jpeg)



](/@h.abdelhamid.mefrommi?source=post_page---read_next_recirc--0f6bec83a6a1----1---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

Abdelhamid Houari

](/@h.abdelhamid.mefrommi?source=post_page---read_next_recirc--0f6bec83a6a1----1---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

·

Jun 14

[

Penetration Testing in Nutshell
-------------------------------

### Low Hanging Fruits



](/@h.abdelhamid.mefrommi/penetration-testing-in-nutshell-3e9fcc1327bd?source=post_page---read_next_recirc--0f6bec83a6a1----1---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

A clap icon1







](/@h.abdelhamid.mefrommi/penetration-testing-in-nutshell-3e9fcc1327bd?source=post_page---read_next_recirc--0f6bec83a6a1----1---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

![Write-up: SQL Injection Fundamentals](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*U2aI34T-FrZgxPMJRelBnQ.png)

[

![Jordi Been](https://miro.medium.com/v2/resize:fill:40:40/1*dmbNkD5D-u45r44go_cf0g.png)



](/@jordi_been?source=post_page---read_next_recirc--0f6bec83a6a1----2---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

Jordi Been

](/@jordi_been?source=post_page---read_next_recirc--0f6bec83a6a1----2---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

·

Mar 4

[

Write-up: SQL Injection Fundamentals
------------------------------------

### Almost every modern web application relies on a back-end database. These databases store everything from website content and product…



](/@jordi_been/write-up-sql-injection-fundamentals-f843b609a51c?source=post_page---read_next_recirc--0f6bec83a6a1----2---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[](/@jordi_been/write-up-sql-injection-fundamentals-f843b609a51c?source=post_page---read_next_recirc--0f6bec83a6a1----2---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

![HTB Academy — Web Fuzzing with ffuf: Full Walkthrough](https://miro.medium.com/v2/resize:fit:1358/format:webp/1*Gm3IH5n_fBsVbLZiKeLy_g.jpeg)

[

![Zeyad Mostafa](https://miro.medium.com/v2/resize:fill:40:40/1*Gm3IH5n_fBsVbLZiKeLy_g.jpeg)



](/@zyadm97?source=post_page---read_next_recirc--0f6bec83a6a1----3---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

Zeyad Mostafa

](/@zyadm97?source=post_page---read_next_recirc--0f6bec83a6a1----3---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

·

Mar 10

[

HTB Academy — Web Fuzzing with ffuf: Full Walkthrough
-----------------------------------------------------

### A step-by-step guide to DNS/VHost fuzzing, parameter discovery, and value fuzzing using ffuf on Hack The Box Academy.



](/@zyadm97/htb-academy-web-fuzzing-with-ffuf-full-walkthrough-c7fdc469e1af?source=post_page---read_next_recirc--0f6bec83a6a1----3---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

A clap icon3







](/@zyadm97/htb-academy-web-fuzzing-with-ffuf-full-walkthrough-c7fdc469e1af?source=post_page---read_next_recirc--0f6bec83a6a1----3---------------------035a8e8e_518a_490d_9ff2_8be4df902409--------------)

[

See more recommendations

](/?source=post_page---read_next_recirc--0f6bec83a6a1---------------------------------------)

[

Help

](https://help.medium.com/hc/en-us?source=post_page-----0f6bec83a6a1---------------------------------------)

[

Status

](https://status.medium.com/?source=post_page-----0f6bec83a6a1---------------------------------------)

[

About

](/about?autoplay=1&source=post_page-----0f6bec83a6a1---------------------------------------)

[

Careers

](/jobs-at-medium/work-at-medium-959d1a85284e?source=post_page-----0f6bec83a6a1---------------------------------------)

[

Press

](mailto:pressinquiries@medium.com)

[

Blog

](https://blog.medium.com/?source=post_page-----0f6bec83a6a1---------------------------------------)

[

Store

](https://medium.com/store)

[

Privacy

](https://policy.medium.com/medium-privacy-policy-f03bf92035c9?source=post_page-----0f6bec83a6a1---------------------------------------)

[

Rules

](https://policy.medium.com/medium-rules-30e5502c4eb4?source=post_page-----0f6bec83a6a1---------------------------------------)

[

Terms

](https://policy.medium.com/medium-terms-of-service-9db0094a1e0f?source=post_page-----0f6bec83a6a1---------------------------------------)

[

Text to speech

](https://speechify.com/medium?source=post_page-----0f6bec83a6a1---------------------------------------)