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
