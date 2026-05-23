"""NSE Stock universe — NIFTY 200 stocks with company name aliases.

Recent corporate actions reflected (as of May 2026):
  - ZOMATO  → ETERNAL    (renamed Apr 2025)
  - MCDOWELL-N → UNITDSPR (renamed Jun 2024)
  - GMRINFRA → GMRAIRPORT (renamed; the demerged power+urban-infra piece trades as GMRP&UI)
  - MAZAGON  → MAZDOCK    (correct ticker for Mazagon Dock Shipbuilders)
  - TATAMOTORS → TMPV (passenger vehicles + JLR) + new TMCV (commercial vehicles)
    (split Oct 2025; TMCV listed Nov 2025 — both in F&O)

Old `aliases` entries are preserved so news fetchers, AI prompts, and search
recall still match historical names (e.g. someone searching "Zomato" will still
find Eternal Limited articles).
"""

NSE_STOCKS = {
    "RELIANCE":   {"name": "Reliance Industries",       "aliases": ["Reliance", "RIL", "Mukesh Ambani"]},
    "TCS":        {"name": "Tata Consultancy Services", "aliases": ["TCS", "Tata Consultancy"]},
    "HDFCBANK":   {"name": "HDFC Bank",                  "aliases": ["HDFC Bank"]},
    "INFY":       {"name": "Infosys",                    "aliases": ["Infosys", "Infy"]},
    "ICICIBANK":  {"name": "ICICI Bank",                 "aliases": ["ICICI Bank", "ICICI"]},
    "HINDUNILVR": {"name": "Hindustan Unilever",        "aliases": ["HUL", "Hindustan Unilever"]},
    "ITC":        {"name": "ITC Limited",                "aliases": ["ITC"]},
    "SBIN":       {"name": "State Bank of India",        "aliases": ["SBI", "State Bank"]},
    "BHARTIARTL": {"name": "Bharti Airtel",              "aliases": ["Bharti Airtel", "Airtel"]},
    "KOTAKBANK":  {"name": "Kotak Mahindra Bank",        "aliases": ["Kotak Bank", "Kotak Mahindra"]},
    "LT":         {"name": "Larsen & Toubro",            "aliases": ["L&T", "Larsen", "Larsen & Toubro"]},
    "AXISBANK":   {"name": "Axis Bank",                  "aliases": ["Axis Bank"]},
    "ASIANPAINT": {"name": "Asian Paints",               "aliases": ["Asian Paints"]},
    "MARUTI":     {"name": "Maruti Suzuki",              "aliases": ["Maruti", "Maruti Suzuki"]},
    "BAJFINANCE": {"name": "Bajaj Finance",              "aliases": ["Bajaj Finance"]},
    "HCLTECH":    {"name": "HCL Technologies",           "aliases": ["HCL Tech", "HCL Technologies"]},
    "SUNPHARMA":  {"name": "Sun Pharmaceutical",         "aliases": ["Sun Pharma"]},
    "TITAN":      {"name": "Titan Company",              "aliases": ["Titan"]},
    "WIPRO":      {"name": "Wipro",                      "aliases": ["Wipro"]},
    "ULTRACEMCO": {"name": "UltraTech Cement",           "aliases": ["UltraTech", "Ultratech Cement"]},
    "NESTLEIND":  {"name": "Nestle India",               "aliases": ["Nestle", "Nestle India"]},
    "ONGC":       {"name": "Oil & Natural Gas Corp",     "aliases": ["ONGC"]},
    "NTPC":       {"name": "NTPC",                       "aliases": ["NTPC"]},
    "POWERGRID":  {"name": "Power Grid Corp",            "aliases": ["Power Grid", "PowerGrid"]},
    "M&M":        {"name": "Mahindra & Mahindra",        "aliases": ["Mahindra & Mahindra", "M&M"]},
    # Tata Motors split into two listed entities in Oct 2025 — both in F&O:
    #   TMPV = passenger vehicles + JLR (former TATAMOTORS, renamed)
    #   TMCV = commercial vehicles (newly listed Nov 2025)
    "TMPV":       {"name": "Tata Motors Passenger Vehicles", "aliases": ["Tata Motors PV", "TMPV", "Tata Motors", "TATAMOTORS"]},
    "TMCV":       {"name": "Tata Motors (Commercial)",       "aliases": ["Tata Motors CV", "TMCV", "TML Commercial Vehicles"]},
    "TATASTEEL":  {"name": "Tata Steel",                 "aliases": ["Tata Steel"]},
    "ADANIENT":   {"name": "Adani Enterprises",          "aliases": ["Adani Enterprises", "Adani"]},
    "ADANIPORTS": {"name": "Adani Ports",                "aliases": ["Adani Ports"]},
    "JSWSTEEL":   {"name": "JSW Steel",                  "aliases": ["JSW Steel"]},
    "COALINDIA":  {"name": "Coal India",                 "aliases": ["Coal India"]},
    "BAJAJFINSV": {"name": "Bajaj Finserv",              "aliases": ["Bajaj Finserv"]},
    "HDFCLIFE":   {"name": "HDFC Life Insurance",        "aliases": ["HDFC Life"]},
    "SBILIFE":    {"name": "SBI Life Insurance",         "aliases": ["SBI Life"]},
    "TECHM":      {"name": "Tech Mahindra",              "aliases": ["Tech Mahindra", "TechM"]},
    "INDUSINDBK": {"name": "IndusInd Bank",              "aliases": ["IndusInd Bank", "IndusInd"]},
    "GRASIM":     {"name": "Grasim Industries",          "aliases": ["Grasim"]},
    "DRREDDY":    {"name": "Dr Reddys Laboratories",     "aliases": ["Dr Reddy", "Dr. Reddy", "DRL"]},
    "CIPLA":      {"name": "Cipla",                      "aliases": ["Cipla"]},
    "EICHERMOT":  {"name": "Eicher Motors",              "aliases": ["Eicher Motors", "Eicher"]},
    "BRITANNIA":  {"name": "Britannia Industries",       "aliases": ["Britannia"]},
    "HEROMOTOCO": {"name": "Hero MotoCorp",              "aliases": ["Hero MotoCorp", "Hero Moto"]},
    "BAJAJ-AUTO": {"name": "Bajaj Auto",                 "aliases": ["Bajaj Auto"]},
    "DIVISLAB":   {"name": "Divis Laboratories",         "aliases": ["Divis Lab", "Divi's Lab"]},
    "APOLLOHOSP": {"name": "Apollo Hospitals",           "aliases": ["Apollo Hospitals"]},
    "TATACONSUM": {"name": "Tata Consumer Products",     "aliases": ["Tata Consumer"]},
    "HINDALCO":   {"name": "Hindalco Industries",        "aliases": ["Hindalco"]},
    "BPCL":       {"name": "Bharat Petroleum",           "aliases": ["BPCL", "Bharat Petroleum"]},
    "UPL":        {"name": "UPL Limited",                "aliases": ["UPL"]},
    "SHRIRAMFIN": {"name": "Shriram Finance",            "aliases": ["Shriram Finance"]},
    "LTIM":       {"name": "LTIMindtree",                "aliases": ["LTIMindtree", "LTI Mindtree"]},
    "DMART":      {"name": "Avenue Supermarts",          "aliases": ["DMart", "Avenue Supermarts"]},
    "PIDILITIND": {"name": "Pidilite Industries",        "aliases": ["Pidilite"]},
    "GODREJCP":   {"name": "Godrej Consumer Products",   "aliases": ["Godrej Consumer"]},
    "DABUR":      {"name": "Dabur India",                "aliases": ["Dabur"]},
    "MARICO":     {"name": "Marico",                     "aliases": ["Marico"]},
    "HAVELLS":    {"name": "Havells India",              "aliases": ["Havells"]},
    "AMBUJACEM":  {"name": "Ambuja Cements",             "aliases": ["Ambuja Cements", "Ambuja"]},
    "SIEMENS":    {"name": "Siemens India",              "aliases": ["Siemens"]},
    "DLF":        {"name": "DLF Limited",                "aliases": ["DLF"]},
    "BANKBARODA": {"name": "Bank of Baroda",             "aliases": ["Bank of Baroda", "BoB"]},
    "PNB":        {"name": "Punjab National Bank",       "aliases": ["PNB", "Punjab National Bank"]},
    "CANBK":      {"name": "Canara Bank",                "aliases": ["Canara Bank"]},
    "IOC":        {"name": "Indian Oil Corporation",     "aliases": ["IOC", "Indian Oil"]},
    "GAIL":       {"name": "GAIL India",                 "aliases": ["GAIL"]},
    "VEDL":       {"name": "Vedanta Limited",            "aliases": ["Vedanta"]},
    "ADANIGREEN": {"name": "Adani Green Energy",         "aliases": ["Adani Green"]},
    "ADANIPOWER": {"name": "Adani Power",                "aliases": ["Adani Power"]},
    "TATAPOWER":  {"name": "Tata Power",                 "aliases": ["Tata Power"]},
    # Zomato Ltd renamed to Eternal Ltd, ticker ZOMATO → ETERNAL effective Apr 9, 2025
    "ETERNAL":    {"name": "Eternal Limited",            "aliases": ["Eternal", "Zomato", "Blinkit", "Hyperpure"]},
    "PAYTM":      {"name": "One 97 Communications",      "aliases": ["Paytm"]},
    "NYKAA":      {"name": "FSN E-Commerce Ventures",    "aliases": ["Nykaa"]},
    "POLICYBZR":  {"name": "PB Fintech",                 "aliases": ["PB Fintech", "PolicyBazaar"]},
    "IRCTC":      {"name": "Indian Railway Catering",    "aliases": ["IRCTC"]},
    "IRFC":       {"name": "Indian Railway Finance Corp","aliases": ["IRFC"]},
    "YESBANK":    {"name": "Yes Bank",                   "aliases": ["Yes Bank"]},
    "IDFCFIRSTB": {"name": "IDFC First Bank",            "aliases": ["IDFC First Bank", "IDFC First"]},
    "RECLTD":     {"name": "REC Limited",                "aliases": ["REC Ltd", "Rural Electrification"]},
    "PFC":        {"name": "Power Finance Corporation",  "aliases": ["PFC", "Power Finance"]},
    "BEL":        {"name": "Bharat Electronics",         "aliases": ["BEL", "Bharat Electronics"]},
    "HAL":        {"name": "Hindustan Aeronautics",      "aliases": ["HAL", "Hindustan Aeronautics"]},
    "BHEL":       {"name": "Bharat Heavy Electricals",   "aliases": ["BHEL"]},
    "SAIL":       {"name": "Steel Authority of India",   "aliases": ["SAIL"]},
    "NHPC":       {"name": "NHPC",                       "aliases": ["NHPC"]},
    "TRENT":      {"name": "Trent Limited",              "aliases": ["Trent"]},
    "JINDALSTEL": {"name": "Jindal Steel & Power",       "aliases": ["Jindal Steel"]},
    "LICI":       {"name": "Life Insurance Corporation", "aliases": ["LIC", "LIC India"]},
    "INDHOTEL":   {"name": "Indian Hotels Company",      "aliases": ["Indian Hotels", "Taj Hotels"]},
    "MOTHERSON":  {"name": "Samvardhana Motherson",      "aliases": ["Motherson"]},
    "BOSCHLTD":   {"name": "Bosch Limited",              "aliases": ["Bosch"]},
    "TVSMOTOR":   {"name": "TVS Motor Company",          "aliases": ["TVS Motor"]},
    "ASHOKLEY":   {"name": "Ashok Leyland",              "aliases": ["Ashok Leyland"]},
    "CUMMINSIND": {"name": "Cummins India",              "aliases": ["Cummins"]},
    "ABB":        {"name": "ABB India",                  "aliases": ["ABB"]},
    "BERGEPAINT": {"name": "Berger Paints",              "aliases": ["Berger Paints"]},
    "COLPAL":     {"name": "Colgate Palmolive",          "aliases": ["Colgate", "Colgate-Palmolive"]},
    "LUPIN":      {"name": "Lupin Limited",              "aliases": ["Lupin"]},
    "AUROPHARMA": {"name": "Aurobindo Pharma",           "aliases": ["Aurobindo Pharma", "Aurobindo"]},
    "TORNTPHARM": {"name": "Torrent Pharmaceuticals",    "aliases": ["Torrent Pharma"]},
    "BIOCON":     {"name": "Biocon Limited",             "aliases": ["Biocon"]},
    "PERSISTENT": {"name": "Persistent Systems",         "aliases": ["Persistent Systems", "Persistent"]},
    "MPHASIS":    {"name": "Mphasis Limited",            "aliases": ["Mphasis"]},
    "COFORGE":    {"name": "Coforge Limited",            "aliases": ["Coforge"]},
    "OFSS":       {"name": "Oracle Financial Services",  "aliases": ["Oracle Financial"]},
    "INDIGO":     {"name": "InterGlobe Aviation",        "aliases": ["IndiGo", "InterGlobe Aviation"]},
    "PGHH":       {"name": "Procter & Gamble Hygiene",   "aliases": ["P&G Hygiene", "Procter & Gamble"]},
    "ICICIGI":    {"name": "ICICI Lombard General Ins",  "aliases": ["ICICI Lombard"]},
    "ICICIPRULI": {"name": "ICICI Prudential Life",      "aliases": ["ICICI Prudential"]},
    "BAJAJHLDNG": {"name": "Bajaj Holdings",             "aliases": ["Bajaj Holdings"]},
    "CHOLAFIN":   {"name": "Cholamandalam Investment",   "aliases": ["Cholamandalam", "Chola Finance"]},
    "MUTHOOTFIN": {"name": "Muthoot Finance",            "aliases": ["Muthoot Finance"]},
    "SBICARD":    {"name": "SBI Cards",                  "aliases": ["SBI Cards"]},
    "PIIND":      {"name": "PI Industries",              "aliases": ["PI Industries"]},
    "NAUKRI":     {"name": "Info Edge India",            "aliases": ["Info Edge", "Naukri"]},
    # McDowell-N renamed to United Spirits, ticker MCDOWELL-N → UNITDSPR effective Jun 7, 2024
    "UNITDSPR":   {"name": "United Spirits",             "aliases": ["United Spirits", "McDowell", "Diageo India"]},
    "GODREJPROP": {"name": "Godrej Properties",          "aliases": ["Godrej Properties"]},
    "OBEROIRLTY": {"name": "Oberoi Realty",              "aliases": ["Oberoi Realty"]},
    "LODHA":      {"name": "Macrotech Developers",       "aliases": ["Lodha", "Macrotech"]},
    "SUZLON":     {"name": "Suzlon Energy",              "aliases": ["Suzlon"]},
    "IDEA":       {"name": "Vodafone Idea",              "aliases": ["Vodafone Idea", "Vi"]},
    "RVNL":       {"name": "Rail Vikas Nigam",           "aliases": ["RVNL", "Rail Vikas"]},
    "NMDC":       {"name": "NMDC Limited",               "aliases": ["NMDC"]},
    # Correct NSE ticker is MAZDOCK (not MAZAGON) for Mazagon Dock Shipbuilders
    "MAZDOCK":    {"name": "Mazagon Dock Shipbuilders",  "aliases": ["Mazagon Dock", "MDL"]},
    "COCHINSHIP": {"name": "Cochin Shipyard",            "aliases": ["Cochin Shipyard"]},
    "BDL":        {"name": "Bharat Dynamics",            "aliases": ["Bharat Dynamics", "BDL"]},
    # GMR Infrastructure renamed to GMR Airports, ticker GMRINFRA → GMRAIRPORT
    "GMRAIRPORT": {"name": "GMR Airports",               "aliases": ["GMR Airports", "GMR Infrastructure"]},
}


def get_yf_symbol(nse_symbol: str) -> str:
    return f"{nse_symbol}.NS"


def get_all_symbols():
    return list(NSE_STOCKS.keys())


def get_search_terms(symbol: str):
    stock = NSE_STOCKS.get(symbol)
    if not stock:
        return [symbol]
    return [stock["name"]] + stock["aliases"]
