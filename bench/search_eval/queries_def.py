"""Vague queries with graded relevance (2 = what the user wants, 1 = partially relevant).
kind: 'doc' (evaluated in scopes documents + all), 'img' (images + all), 'none-doc' / 'none-img'
(no relevant file; evaluated in documents|images + all)."""

Q = []


def q(qid, kind, text, rel=None):
    Q.append({"id": qid, "kind": kind, "text": text, "rel": rel or {}})


# ---------------- documents
q("dq_rent", "doc", "that thing about my rent agreement",
  {"lease_main": 2, "lease_license": 2, "lease_renewal": 1, "rcpt_rent_payment": 1, "roommate_rules": 1})
q("dq_ml", "doc", "notes from the machine learning class",
  {"ml_week3": 2, "ml_lec5": 2, "ml_nn": 2, "ml_full": 2, "ml_hw2": 1})
q("dq_cv_intern", "doc", "the cv I sent for the internship", {"cv_main": 2, "cover_intern": 2, "cv_old": 1})
q("dq_blood", "doc", "my blood test results", {"med_cbc": 2, "med_lipid": 2})
q("dq_printer", "doc", "how to fix the printer jam", {"man_printer": 2})
q("dq_flight", "doc", "flight ticket to singapore", {"trav_flight": 2, "trav_insurance": 1})
q("dq_sprint", "doc", "what did we decide in the sprint planning meeting", {"min_sprint": 2})
q("dq_cookie", "doc", "cookie recipe", {"rec_cookies": 2})
q("dq_washer", "doc", "something about the washing machine error codes", {"man_washer": 2})
q("dq_hotel", "doc", "the hotel booking in lisbon", {"trav_hotel": 2})
q("dq_form16", "doc", "form 16 from my employer", {"tax_form16": 2, "tax_itr": 1})
q("dq_tax", "doc", "tax documents for last year",
  {"tax_form16": 2, "tax_w2": 2, "tax_calc": 2, "tax_itr": 2, "tax_property": 1})
q("dq_hosting", "doc", "invoice for the web hosting", {"inv_hosting": 2})
q("dq_laptop", "doc", "receipt for the laptop I bought", {"rcpt_laptop": 2})
q("dq_chem", "doc", "chemistry lecture about reactions", {"chem_sn": 2, "chem_alkenes": 2})
q("dq_econ", "doc", "economics class notes on inflation", {"econ_inflation": 2, "econ_fiscal": 1})
q("dq_microplastic", "doc", "that paper about plastic pollution in the river", {"res_microplastic": 2})
q("dq_fed", "doc", "research abstract on privacy in federated learning", {"res_fedlearn": 2})
q("dq_router", "doc", "how do I set up the wifi router", {"man_router": 2, "dist_wifi": 1})
q("dq_japan", "doc", "japan trip plan", {"trav_japan": 2})
q("dq_society", "doc", "the society meeting about the water tank", {"min_society": 2})
q("dq_cover_analyst", "doc", "my cover letter for the data analyst job", {"cover_analyst": 2, "cover_intern": 1})
q("dq_rx", "doc", "the medicine the doctor prescribed for my fever", {"med_rx": 2})
q("dq_plant", "doc", "readme for the plant watering project", {"readme_plant": 2})
q("dq_ww1", "doc", "lecture on world war one", {"hist_ww1": 2, "hist_french": 1})
q("dq_banana", "doc", "banana bread", {"rec_banana": 2})
q("dq_train", "doc", "my train ticket to mumbai", {"trav_train": 2})
q("dq_discharge", "doc", "hospital papers from my appendix surgery", {"med_discharge": 2})
q("dq_hyp", "doc", "notes about hypothesis testing and p values", {"stats_hyp": 2})
q("dq_vaccine", "doc", "my covid vaccine certificate", {"med_vaccine": 2})
q("dq_api", "doc", "documentation of the orders REST API", {"readme_api": 2})
q("dq_headphones", "doc", "how to pair my headphones", {"man_headphones": 2, "rcpt_headphones": 1})
q("dq_solar", "doc", "something on solar cells for my thesis", {"res_perovskite": 2})
q("dq_dinner_bill", "doc", "dinner bill from the restaurant", {"rcpt_restaurant": 2})
q("dq_recipes", "doc", "all my recipes",
  {"rec_curry": 2, "rec_cookies": 2, "rec_pasta": 2, "rec_banana": 2, "rec_mealplan": 1})
q("dq_medical", "doc", "my medical records",
  {"med_cbc": 2, "med_lipid": 2, "med_discharge": 2, "med_rx": 2, "med_vaccine": 2})
q("dq_phys", "doc", "physics notes on newton's laws", {"phys_mech": 2})
q("dq_leave", "doc", "how many days of leave do I get at work", {"handbook": 2})

# ---------------- images
q("iq_sky_evening", "img", "pictures of the sky in the evening", {"sem_sunset": 2, "sem_stars": 1, "img_moon": 1})
q("iq_beach", "img", "that photo from the beach", {"img_beach": 2, "img_sailboat": 1})
q("iq_mountains", "img", "snowy mountains", {"img_mountains": 2})
q("iq_forest", "img", "trees in the woods", {"img_forest": 2})
q("iq_city_night", "img", "the city lit up at night", {"img_skyline": 2})
q("iq_cake", "img", "birthday cake picture", {"img_cake": 2})
q("iq_chat", "img", "screenshot of a whatsapp chat", {"img_chat": 2})
q("iq_flowchart", "img", "the flowchart diagram", {"img_flowchart": 2})
q("iq_boat", "img", "boat on the sea", {"img_sailboat": 2, "img_beach": 1})
q("iq_fireworks", "img", "fireworks at diwali", {"img_fireworks": 2})
q("iq_flower", "img", "a pretty flower", {"img_flower": 2})
q("iq_rainbow", "img", "rainbow after the rain", {"img_rainbow": 2})
q("iq_grocery", "img", "photo of the grocery bill", {"sem_receipt": 2})
q("iq_bars", "img", "chart with bars", {"sem_bar_chart": 2, "img_linechart": 1})
q("iq_pie", "img", "pie chart", {"sem_pie_chart": 2})
q("iq_code", "img", "screenshot of my python code", {"sem_code": 2})
q("iq_qr", "img", "qr code", {"sem_qr": 2, "img_barcode": 1})
q("iq_table", "img", "picture of a table of numbers", {"sem_spreadsheet": 2, "img_calendar": 1})
q("iq_house", "img", "kid's drawing of a house", {"img_house": 2})
q("iq_clock", "img", "a clock on the wall", {"img_clock": 2})
q("iq_chess", "img", "chess game", {"img_chess": 2})
q("iq_field", "img", "green field with clouds", {"sem_meadow": 2})
q("iq_moon", "img", "the moon at night", {"img_moon": 2, "sem_stars": 1})
q("iq_calendar", "img", "calendar for march", {"img_calendar": 2})
q("iq_slide", "img", "slide from the presentation", {"img_slide": 2})
q("iq_barcode", "img", "barcode", {"img_barcode": 2, "sem_qr": 1})
q("iq_map", "img", "map of the streets", {"sem_map": 2})
q("iq_traffic", "img", "traffic signal", {"img_traffic": 2})
q("iq_page", "img", "scan of a printed page", {"sem_document": 2})

# ---------------- no relevant file
q("na_car_ins", "none-doc", "car insurance renewal")
q("na_divorce", "none-doc", "divorce papers")
q("na_guitar", "none-doc", "guitar chords for my song")
q("na_appraisal", "none-doc", "appraisal letter with my salary increment")
q("na_dog", "none-img", "photos of my dog")
q("na_wedding", "none-img", "wedding photos")
q("na_selfie", "none-img", "selfie with friends")
q("na_car_photo", "none-img", "picture of my new car")


def split():
    """Deterministic split: within each kind, alternate by definition order (even -> tune, odd -> held)."""
    tune, held = [], []
    seen = {}
    for x in Q:
        k = x["kind"]
        i = seen.get(k, 0)
        seen[k] = i + 1
        (tune if i % 2 == 0 else held).append(x)
    return tune, held
