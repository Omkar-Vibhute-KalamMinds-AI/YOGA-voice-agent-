import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from langchain_core.tools import tool

@tool
def age_calculator(date: int, month: int, year: int) -> str:
    """Calculate the age of a person
    Args:
        date: The date of birth
        month: The month of birth
        year: The year of birth
    Returns:
        The age of the person
    """ 

   
    from datetime import datetime
    today = datetime.now()
    dob = datetime(year, month, date)
    years = today.year - dob.year
    months = today.month - dob.month
    days = today.day - dob.day
    
    if months < 0:
        months = 12 + months
        years -= 1
    if days < 0:
        days = 30 + days
        months -= 1
        
    return f"{years} years,{months} months, and {days} days"

#---------------------------------Tools---------------------------------

@tool
def wikipedia_search(query: str) -> str:
    """Search the official English Wikipedia API and return the top page titles, links, and short summaries.
    Args:
        query: The query to search for
    Returns:
        The top page titles, links, and short summaries
    """
    params = urlencode({
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": 3,
        "prop": "extracts|info",
        "inprop": "url",
        "exintro": 1,
        "explaintext": 1,
        "exchars": 500,
        "format": "json",
    })
    request = Request(
        f"https://en.wikipedia.org/w/api.php?{params}",
        headers={"User-Agent": "Chatbot/1.0 (educational LangChain tool)"},
    )
    with urlopen(request, timeout=20) as response:
        data = json.load(response)

    pages = (data.get("query") or {}).get("pages") or {}
    if not pages:
        return f"No Wikipedia results for {query}."

    chunks = []
    for page in sorted(pages.values(), key=lambda item: item.get("index", 0)):
        title = page.get("title", "Untitled")
        url = page.get("fullurl", "")
        extract = (page.get("extract") or "").strip()
        chunks.append(f"{title}\n{url}\n{extract}")
    return "\n\n".join(chunks)


tools = [age_calculator, wikipedia_search] 
