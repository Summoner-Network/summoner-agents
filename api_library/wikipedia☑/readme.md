# Wikipedia API

API docs [here](https://en.wikipedia.org/api/rest_v1/)

---

This agent uses Wikipedia's **public** REST endpoints — no keys or OAuth required.

1. **Search** for matching titles  
```
GET [https://en.wikipedia.org/w/rest.php/v1/search/title](https://en.wikipedia.org/w/rest.php/v1/search/title)
?q=<your query>
\&limit=<max results>
```
• See the API response format:  [here](https://en.wikipedia.org/api/rest_v1/search/title)
 

2. **Fetch summary** of a page  
```
GET [https://en.wikipedia.org/api/rest\_v1/page/summary/](https://en.wikipedia.org/api/rest_v1/page/summary/)\<Page\_Title>
```
• Docs & examples:  [here](https://en.wikipedia.org/api/rest_v1/#/Page%20content/get_page_summary__title_)

## How to extend the agent in test.py

1. **Other endpoints**  
- Sectioned content:  
  `GET /page/mobile-sections/{title}`  
- Full HTML (infoboxes, tables):  
  `GET /page/html/{title}`  

2. **Multilingual support**  
Swap `en.wikipedia.org` for `fr.`, `es.`, etc., to fetch non-English articles.

3. **Rate limiting & caching**  
- Wikimedia allows generous read rates, but be polite:  
  - Cache summaries for a few hours.  
  - Add a small back-off on repeated search calls.

4. **Error handling**  
- 404 on missing page  
- Empty "extract" fields  
- Network failures (retry once after a delay)

5. **Possible enhancements**  
- Disambiguation support: detect when the API returns a disambiguation page.  
- Infobox parsing: scrape the REST HTML endpoint for key/value pairs.  
- Local disk–backed cache or database for heavy-use agents.

These two REST docs are the single most direct, up-to-date references. You don't need the older MediaWiki "action=..." API unless you want write-access or very fine-grained queries.

