"""Job discovery: page data, the site's Careers link, and the careers site's public job sitemap (fakes only)."""
from app.job_discover import _title_from_address, discover_jobs

HOME = '<html><head><title>Acme Corp | Home</title></head><body><a href="https://careers.acme.test/"><span>Careers</span></a></body></html>'
CAREERS = "<html><body>Search jobs (rendered by JavaScript)</body></html>"
ROBOTS = "User-agent: *\nSitemap: https://careers.acme.test/sitemap_index.xml\n"
INDEX = "<sitemapindex><sitemap><loc>https://careers.acme.test/sitemap_jobs.xml</loc></sitemap></sitemapindex>"
JOBS = ("<urlset><url><loc>https://careers.acme.test/job/Pune-SOLUTION-ARCHITECT-IND-411005/111/</loc><lastmod>2026-11-01</lastmod></url>"
        "<url><loc>https://careers.acme.test/job/Atlanta-ADMINISTRATOR-L1-USA-14808/222/</loc></url>"
        "<url><loc>https://careers.acme.test/about/</loc></url></urlset>")


def fake(pages):
    def get(url):
        if url not in pages:
            raise OSError("not found")
        return pages[url]
    return get


def test_follows_careers_link_then_reads_the_job_sitemap():
    get = fake({"https://www.acme.test/": HOME, "https://careers.acme.test/": CAREERS, "https://careers.acme.test/robots.txt": ROBOTS,
                "https://careers.acme.test/sitemap_index.xml": INDEX, "https://careers.acme.test/sitemap_jobs.xml": JOBS})
    jobs, note = discover_jobs("https://www.acme.test/", get)
    assert [(j["title"], j["location"], j["date_posted"]) for j in jobs] == [
        ("Solution Architect", "Pune, India", "2026-11-01"), ("Administrator L1", "Atlanta, United States", "")]
    assert all(j["company"] == "Acme Corp" for j in jobs) and "sitemap" in note


def test_page_job_data_wins_and_microdata_is_read():
    micro = ('<div itemscope itemtype="https://schema.org/JobPosting"><h1 itemprop="title">Store Manager</h1>'
             '<span itemprop="addressLocality">Noida</span><meta itemprop="datePosted" content="2026-10-20"></div>')
    jobs, note = discover_jobs("https://shop.test/jobs/1", fake({"https://shop.test/jobs/1": micro}))
    assert jobs[0]["title"] == "Store Manager" and jobs[0]["location"] == "Noida" and "published" in note


def test_nothing_found_explains_what_to_do():
    jobs, note = discover_jobs("https://plain.test/", fake({"https://plain.test/": "<html><body>Hello</body></html>"}))
    assert jobs == [] and "Paste the address of one job page" in note


def test_title_from_address():
    assert _title_from_address("https://c.test/job/Hyderabad-TECHNICAL-LEAD-L1-IND/9/") == ("Technical Lead L1", "Hyderabad, India")
    assert _title_from_address("https://c.test/jobs/senior-data-engineer/") == ("senior data engineer", "")


GH_JOBS = ('{"jobs":[{"title":"Account Executive, India","company_name":"Acme Labs","location":{"name":"Bengaluru, India"},'
           '"first_published":"2026-10-19T11:17:24-04:00","absolute_url":"https://job-boards.greenhouse.io/acmelabs/jobs/1"},'
           '{"title":"Support Engineer","location":{"name":"Remote"},"updated_at":"2026-10-01T00:00:00Z",'
           '"absolute_url":"https://job-boards.greenhouse.io/acmelabs/jobs/2"}]}')
LEVER_HTML = ('<html><head><title>Acme Robotics</title></head><body><div class="posting"><a class="posting-title" '
              'href="https://jobs.lever.co/acmerobotics/aaa"><h5 data-qa="posting-name">Sales Development Rep</h5>'
              '<div class="posting-categories"><span class="sort-by-commitment posting-category commitment">Full-time</span>'
              '<span href="#" class="sort-by-location posting-category small-category-label location">Mumbai, India</span></div></a></div>'
              '<div class="posting"><a class="posting-title" href="https://jobs.lever.co/acmerobotics/bbb"><h5 data-qa="posting-name">'
              'Field Engineer &amp; Trainer</h5><div class="posting-categories"></div></a></div></body></html>')


def test_greenhouse_board_reads_the_public_board_api():
    """Real Greenhouse boards are drawn by JavaScript: the page has no job data, the board API has all of it."""
    get = fake({"https://boards-api.greenhouse.io/v1/boards/acmelabs/jobs": GH_JOBS,
                "https://job-boards.greenhouse.io/acmelabs": "<html><div id=root></div></html>"})
    for start in ("https://job-boards.greenhouse.io/acmelabs", "https://boards.greenhouse.io/acmelabs",
                  "https://boards.greenhouse.io/embed/job_board?for=acmelabs"):
        jobs, note = discover_jobs(start, get)
        assert [(j["title"], j["company"], j["location"], j["date_posted"]) for j in jobs] == [
            ("Account Executive, India", "Acme Labs", "Bengaluru, India", "2026-10-19"),
            ("Support Engineer", "Acmelabs", "Remote", "2026-10-01")]
        assert "Greenhouse" in note


def test_lever_board_reads_every_posting_from_the_board_page():
    jobs, note = discover_jobs("https://jobs.lever.co/acmerobotics", fake({"https://jobs.lever.co/acmerobotics": LEVER_HTML}))
    assert [(j["title"], j["company"], j["location"]) for j in jobs] == [
        ("Sales Development Rep", "Acme Robotics", "Mumbai, India"), ("Field Engineer & Trainer", "Acme Robotics", "")]
    assert jobs[0]["url"] == "https://jobs.lever.co/acmerobotics/aaa" and "Lever" in note


def test_workday_and_mixed_case_addresses_keep_the_whole_title():
    wd = "https://acme.wd5.myworkdayjobs.com/External/job/"
    assert _title_from_address(wd + "US-CA-Santa-Clara/Manager--Chip-Software_JR2022613") == ("Manager, Chip Software", "Santa Clara, CA, US")
    assert _title_from_address(wd + "US-CA-Remote/Senior-Systems-Engineer---Clocks-IP_JR2024972-1") == (
        "Senior Systems Engineer - Clocks IP", "Remote, CA, US")
    assert _title_from_address("https://c.test/jobs/Senior-Data-Engineer/") == ("Senior Data Engineer", "")


def test_nothing_found_does_not_name_the_same_site_twice():
    home = '<html><title>Plain</title><a href="https://plain.test/careers">Careers</a></html>'
    jobs, note = discover_jobs("https://plain.test/", fake({"https://plain.test/": home, "https://plain.test/careers": "<html>x</html>"}))
    assert jobs == [] and "plain.test or plain.test" not in note


def test_refused_and_missing_pages_say_why(monkeypatch):
    import pytest
    from app import scraper
    from app.channels.netguard import FetchFailed, Response
    for code, words in ((403, "refused automated reading (HTTP 403)"), (404, "not found (HTTP 404)"), (500, "Couldn't fetch")):
        monkeypatch.setattr(scraper, "guarded_request", lambda *a, code=code, **k: Response(code, {}, b"no", a[0]))
        with pytest.raises(FetchFailed, match=re_escape(words)):
            scraper._get("https://www.bigco.test/")


def re_escape(text):
    import re
    return re.escape(text)


def test_company_name_is_never_the_word_careers():
    from app.job_discover import _site_name
    assert _site_name("<title>Careers | HCLTech</title>", "https://careers.hcltech.com/") == "HCLTech"
    assert _site_name("<title>Careers</title>", "https://careers.hcltech.com/") == "Hcltech"
    assert _site_name("<title>Jobs at Zeta - Careers</title>", "https://zeta.tech/") == "Zeta"
    assert _site_name("<title></title>", "https://nvidia.wd5.myworkdayjobs.com/External") == "Nvidia"
