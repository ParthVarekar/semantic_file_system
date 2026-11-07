"""Synthetic evaluation corpus definition (documents). Each entry:
(doc_id, relative path, content). Content is a string; paragraphs are separated by blank lines.
For .pptx, paragraphs are "Title | body". For .xlsx, content is a list of rows.
"""

DOCS = []


def D(doc_id, path, content):
    DOCS.append((doc_id, path, content))


# ---------------------------------------------------------------- A. invoices / receipts
D("inv_hosting", "Downloads/scan_0012.pdf", """
BRIGHTLINE WEB SERVICES PVT LTD - TAX INVOICE
Invoice No: BWS/24-25/0912   Invoice date: 04 June 2024
Bill to: Aarav Mehta, Pune, Maharashtra

Description: Shared web hosting - Business plan, 12 months (domain aaravm.dev included).
SSL certificate renewal, daily backups add-on.

Subtotal: INR 7,980.00   GST 18%: INR 1,436.40   Total amount payable: INR 9,416.40
Payment due within 15 days. Please quote the invoice number when paying by bank transfer.
""")
D("inv_freelance", "Documents/Invoice_INV-2024-0457.pdf", """
INVOICE INV-2024-0457
From: Aarav Mehta (freelance UI designer)   To: Kestrel Analytics LLP
Project: Dashboard redesign - wireframes, high fidelity mockups and two revision rounds.

Hours worked: 32 at INR 1,500 per hour = INR 48,000
Payment terms: net 30. Bank: HDFC Bank, account ending 4471.
Thank you for your business!
""")
D("rcpt_grocery", "Downloads/receipt.txt", """
FRESH MART SUPERMARKET  - Store 142, Baner Road
Cashier: 07   Date: 12/03/2024 18:42

Toor dal 1kg          165.00
Basmati rice 5kg      620.00
Onions 2kg             80.00
Milk 1L x2            128.00
Paneer 200g            95.00
Total                1088.00
Paid by UPI. Thank you for shopping with us. No returns on perishables.
""")
D("rcpt_laptop", "Documents/Document (3).docx", """
Croma Electronics - Purchase Receipt

Receipt number CRM-77812, dated 21 August 2023. Item: Lenovo IdeaPad Slim 5 laptop, 16 GB RAM, 512 GB SSD, serial number PF3K9XQ2.

Price: INR 62,990 including GST. Payment: credit card EMI, 6 months.

Manufacturer warranty: 1 year onsite. Keep this receipt for warranty claims and returns within 7 days of purchase.
""")
D("rcpt_headphones", "Downloads/order_confirmation_88213.pdf", """
Your order has been placed! Order #402-8821390-1173

Sony WH-CH720N wireless noise cancelling headphones, black. Qty 1. Price INR 8,990.
Shipping: free standard delivery. Expected delivery: Thursday 14 March.
Delivery address: Flat 12, Shanti Apartments, Kothrud, Pune.
Payment method: UPI. Order total INR 8,990.
""")
D("rcpt_restaurant", "Downloads/scan_0031.pdf", """
THE BLUE DOOR BISTRO
Table 9   Guests 4   Server: Anita   14-Feb-2024 21:10

Margherita pizza         450
Penne arrabbiata         390
Caesar salad             320
Mango lassi x4           480
Tiramisu x2              500
Food total              2140
Service charge 5%        107
CGST 2.5% + SGST 2.5%    107
Grand total             2354
Thank you for dining with us!
""")
D("rcpt_rent_payment", "Documents/payment_receipt_march.pdf", """
RENT RECEIPT
Received with thanks from Mr. Aarav Mehta the sum of INR 18,000 (Rupees eighteen thousand only)
towards rent for the month of March 2024 for the premises Flat 12, Shanti Apartments, Kothrud, Pune.

Mode of payment: bank transfer.
Landlord: Suresh Kulkarni   Signature: ________   PAN: XXXXX0000X
""")
D("bill_phone", "Downloads/Document (8).pdf", """
Jio Postpaid - Monthly Statement
Account number 0000 0000 0000   Bill period 01 Jan 2024 - 31 Jan 2024

Plan charges: Postpaid 399 plan - INR 399.00
Data add-on 10 GB - INR 101.00
Taxes - INR 90.00
Total amount due INR 590.00 by 20 Feb 2024. Pay using the MyJio app to avoid late fees.
""")

# ---------------------------------------------------------------- B. rent / lease
D("lease_main", "Documents/final_v2.docx", """
RESIDENTIAL LEASE AGREEMENT

This lease agreement is made on 1 July 2023 between Mr. Suresh Kulkarni (the Landlord) and Mr. Aarav Mehta (the Tenant) for the residential flat No. 12, Shanti Apartments, Kothrud, Pune 411038.

Term: The tenancy shall be for a period of eleven (11) months commencing 1 July 2023, renewable by mutual consent.

Rent: The Tenant shall pay a monthly rent of INR 18,000 on or before the 5th day of each month. A security deposit of INR 54,000 has been paid, refundable at the end of the tenancy after deductions for damages, if any.

Maintenance and utilities: Electricity and water charges shall be borne by the Tenant. Society maintenance shall be paid by the Landlord.

Notice: Either party may terminate this agreement by giving one month's written notice. The Tenant shall not sublet the premises.

Signed by the Landlord and the Tenant in the presence of two witnesses.
""")
D("lease_license", "Downloads/scan_0007.pdf", """
LEAVE AND LICENSE AGREEMENT
This agreement of leave and license is executed at Pune between the Licensor, Mrs. Meera Joshi, and the Licensee, Mr. Aarav Mehta.

The Licensor hereby grants the Licensee permission to occupy the premises at B-304, Green Valley Society, Aundh, on leave and license basis for 11 months.

License fee: INR 15,500 per month payable in advance. Refundable deposit: INR 40,000.
Lock-in period of 3 months. Registered with the Sub-Registrar, Haveli No. 11.
""")
D("lease_renewal", "Documents/Document.docx", """
Dear Mr. Mehta,

Subject: Renewal of tenancy for Flat 12, Shanti Apartments

Your current tenancy ends on 31 May 2024. I am happy to renew it for another 11 months from 1 June 2024. The monthly rent will be revised to INR 19,500 (an increase of about 8%). The security deposit remains unchanged.

Please confirm by email so that a fresh agreement can be prepared and registered.

Regards, Suresh Kulkarni
""")
D("car_rental", "Downloads/RA_558120.pdf", """
SPEEDY CAR RENTALS - VEHICLE RENTAL CONTRACT
Rental agreement number RA-558120. Renter: Aarav Mehta. Driving licence MH00 0000 0000000.

Vehicle: Maruti Swift Dzire, white, registration MH-12-QX-4410. Pickup: Goa airport, 22 Dec 10:00.
Return: 26 Dec 18:00, same location, full tank.

Daily rate INR 2,200, 4 days, unlimited kilometres. Damage waiver included; excess INR 10,000.
Fuel policy full-to-full. Late return charged per hour.
""")
D("equipment_lease", "Documents/quote_copier.pdf", """
Office equipment lease quotation
Canon imageRUNNER 2425 multifunction copier, 36-month operating lease for Kestrel Analytics LLP.

Monthly lease payment INR 4,250 including preventive maintenance and toner.
Excess copies billed at INR 0.45 per page above 5,000 pages per month.
""")
D("roommate_rules", "Documents/house_rules.txt", """
Flat 12 house rules (agreed by Aarav, Kunal and Dev)

1. Rent of 18,000 is split equally, 6,000 each, paid to Aarav by the 3rd so he can pay the landlord.
2. Electricity and wifi bills are split equally every month.
3. Kitchen cleaning rota: Mon/Thu Kunal, Tue/Fri Dev, Wed/Sat Aarav.
4. Guests staying overnight - tell the others in advance.
5. Quiet hours after 11 pm on weekdays.
""")

# ---------------------------------------------------------------- C. lecture notes
D("ml_week3", "College/Sem5/week3.md", """
# Week 3 - Linear regression and gradient descent

We model the target as y = w x + b and minimise the mean squared error loss over the training set.

## Gradient descent
Update rule: w := w - alpha * dL/dw. The learning rate alpha controls the step size; too large and the loss diverges, too small and training is slow. Batch vs stochastic vs mini-batch gradient descent.

## Feature scaling
Standardise features so gradient descent converges faster. Normal equation as a closed form alternative.

## Regularisation
Ridge (L2) and Lasso (L1) penalties reduce overfitting by shrinking the weights.
Prof. Rao said the midterm will include a derivation of the gradient of the MSE loss.
""")
D("ml_lec5", "College/Sem5/Lecture_05.pptx", [
    ("Lecture 5: Decision trees", "Splitting criteria: Gini impurity and information gain (entropy)"),
    ("Overfitting", "Deep trees memorise the training data; prune or limit depth"),
    ("Cross-validation", "k-fold cross validation to estimate generalisation error and tune hyperparameters"),
    ("Ensembles", "Random forests (bagging) and gradient boosted trees"),
])
D("ml_nn", "College/Sem5/notes (2).docx", """
CS 354 - Introduction to Machine Learning, lecture 11 notes

Neural networks: a multilayer perceptron stacks linear layers with non-linear activation functions such as ReLU and sigmoid.

Backpropagation computes gradients of the loss with respect to every weight using the chain rule; the forward pass stores activations that the backward pass reuses.

Training tips: initialise weights carefully (Xavier/He), use mini-batches, monitor validation loss for early stopping, and use dropout as a regulariser.

Next class: convolutional neural networks for image classification.
""")
D("ml_hw2", "College/Sem5/hw2.pdf", """
CS 354 Homework 2 (due Friday)
1. Implement logistic regression with gradient descent on the provided dataset and plot the training loss.
2. Derive the gradient of the cross-entropy loss with respect to the weights.
3. Compare L1 and L2 regularisation on the validation set. Report accuracy and the confusion matrix.
""")
D("chem_sn", "College/Sem5/chem_notes_oct.docx", """
Organic Chemistry II - Lecture 6: nucleophilic substitution

SN2 reactions occur in one concerted step with backside attack, leading to inversion of configuration (Walden inversion). Rate = k[substrate][nucleophile]. Favoured by primary alkyl halides and polar aprotic solvents.

SN1 reactions proceed through a carbocation intermediate, giving racemisation. Rate depends only on the substrate. Favoured by tertiary substrates and polar protic solvents.

Elimination reactions E1 and E2 compete with substitution, especially at high temperature with strong bulky bases.
""")
D("chem_alkenes", "College/Sem5/Lecture 9.pdf", """
Lecture 9 - Reactions of alkenes
Electrophilic addition of HBr follows Markovnikov's rule: the hydrogen adds to the carbon with more hydrogens.
In the presence of peroxides, anti-Markovnikov addition occurs by a radical mechanism.
Hydration, halogenation (anti addition via a bromonium ion), hydrogenation with Pd/C catalyst.
Ozonolysis cleaves the double bond to give aldehydes or ketones.
""")
D("econ_inflation", "College/Sem3/econ101_lec4.pdf", """
ECON 101 Principles of Macroeconomics - Lecture 4
Gross domestic product measures the market value of final goods and services. Real vs nominal GDP; the GDP deflator.
Inflation is a sustained rise in the general price level, measured by the consumer price index (CPI).
Causes: demand-pull and cost-push inflation. Effects on savers, borrowers and fixed incomes.
Monetary policy: the central bank raises the repo rate to fight inflation and lowers it to stimulate growth.
""")
D("econ_fiscal", "College/Sem3/Document (5).docx", """
Macro class notes - fiscal policy

Fiscal policy uses government spending and taxation to influence aggregate demand. Expansionary policy during recessions, contractionary during booms.

The spending multiplier equals 1 / (1 - MPC). With a marginal propensity to consume of 0.8 the multiplier is 5.

Crowding out: government borrowing can raise interest rates and reduce private investment. Budget deficit vs national debt.
""")
D("hist_ww1", "College/Sem3/hist_notes.txt", """
HIS 210 Modern World History - lecture notes, week 6

Causes of the First World War: militarism, alliances (Triple Entente vs Triple Alliance), imperialism and nationalism (MAIN).
Immediate trigger: assassination of Archduke Franz Ferdinand in Sarajevo, June 1914.
Trench warfare on the Western Front; battles of the Somme and Verdun.
The war ended with the armistice of November 1918 and the Treaty of Versailles in 1919.
""")
D("hist_french", "College/Sem3/untitled.md", """
# History - French Revolution

- Estates-General summoned in 1789; Third Estate forms the National Assembly (Tennis Court Oath)
- Storming of the Bastille, 14 July 1789
- Declaration of the Rights of Man and of the Citizen
- Reign of Terror under Robespierre (1793-94), Committee of Public Safety
- Rise of Napoleon Bonaparte, coup of 18 Brumaire (1799)
Essay question for the exam: was the revolution a success?
""")
D("stats_hyp", "College/Sem3/stats_lecture_2.pptx", [
    ("Hypothesis testing", "Null hypothesis H0 and alternative hypothesis H1"),
    ("p-values", "Probability of data at least as extreme as observed if H0 is true; reject H0 when p < alpha"),
    ("Errors", "Type I error (false positive) and Type II error (false negative); statistical power"),
    ("t-test", "One-sample and two-sample t tests; assumptions of normality and equal variance"),
])
D("phys_mech", "College/Sem1/phys_mech_notes.pdf", """
PHY 101 Mechanics - notes
Newton's first law: a body remains at rest or in uniform motion unless acted upon by a net force.
Second law: F = m a. Third law: every action has an equal and opposite reaction.
Kinematics equations for constant acceleration: v = u + a t, s = u t + 1/2 a t^2, v^2 = u^2 + 2 a s.
Projectile motion: horizontal and vertical components are independent.
""")

# ---------------------------------------------------------------- D. resumes / cover letters
D("cv_main", "Documents/CV_final.docx", """
AARAV MEHTA
Pune, India | aarav.m@example.com | github.com/aaravm

Education: B.Tech Computer Engineering, College of Engineering Pune, 2021-2025, CGPA 8.7.

Experience: Software engineering intern, Kestrel Analytics (May-July 2024): built ETL pipelines in Python and SQL, reduced report generation time by 40%.

Projects: Local file search engine with semantic embeddings; plant watering system on Arduino; weather app in React Native.

Skills: Python, SQL, JavaScript, React, Git, Docker, machine learning basics.

Achievements: Smart India Hackathon finalist 2023.
""")
D("cv_old", "Documents/old/resume_2023.pdf", """
Aarav Mehta - Resume
Second-year Computer Engineering student seeking a summer internship.
Skills: C, C++, Python, HTML/CSS. Coursework: data structures, DBMS, operating systems.
Projects: library management system (Java), personal portfolio website.
Positions: technical team member, coding club.
""")
D("cover_intern", "Documents/letter.docx", """
Dear Hiring Manager,

I am writing to apply for the Summer Software Engineering Internship at Finvolt Technologies, which I found on your careers page. I am a third-year computer engineering student at Westbridge Institute of Technology with a strong interest in fintech and backend systems.

Last summer I interned at Kestrel Analytics where I built data pipelines in Python and SQL. I would love to bring the same energy to your payments team during the internship.

My CV is attached. Thank you for considering my application.

Sincerely, Aarav Mehta
""")
D("cover_analyst", "Documents/Document (7).docx", """
Dear Ms. Fernandes,

Please accept this letter as my application for the Data Analyst position (job ID DA-2291) at Northwind Retail. I enjoy turning messy data into clear dashboards and decisions.

In my internship I automated weekly sales reports with Python and built Power BI dashboards used by the regional managers.

I would welcome the chance to discuss how I can contribute to your analytics team.

Best regards, Aarav Mehta
""")
D("cv_candidate", "Downloads/candidate_profile_rk.pdf", """
Rahul Kamat - Profile
Senior Java developer with 7 years of experience in Spring Boot microservices and Kafka.
Previous employers: Infosys, Persistent Systems. Notice period 60 days. Expected CTC 28 LPA.
Interview feedback: strong system design, average communication.
""")
D("rec_letter", "Documents/reference_letter.pdf", """
To whom it may concern,
I have known Aarav Mehta as a student in my Machine Learning course at Westbridge Institute of Technology where he ranked in the top 5 percent.
He is diligent, curious and works well in teams. I recommend him without reservation for graduate studies.
Dr. A. Rao, Associate Professor, Department of Computer Engineering
""")

# ---------------------------------------------------------------- E. research abstracts
D("res_fedlearn", "Documents/paper_draft.docx", """
Privacy-preserving federated learning with adaptive differential privacy

Abstract. Federated learning trains a shared model across many devices without collecting raw data, but model updates can still leak private information. We propose an adaptive clipping scheme for differentially private federated averaging that allocates the privacy budget per round.

Experiments on FEMNIST and Shakespeare show a 3.1 point accuracy gain over fixed clipping at the same privacy level (epsilon = 2).
""")
D("res_protein", "Downloads/2105.01234.pdf", """
Abstract
Predicting protein structure from amino acid sequence remains a central problem in computational biology.
We present a lightweight geometric transformer that predicts residue distance maps and refines 3D coordinates.
On CASP14 targets our method reaches a median GDT-TS of 71 using 20x less compute than prior work.
Keywords: protein folding, deep learning, structural biology.
""")
D("res_heat", "Documents/abstract_submission.txt", """
Title: Mapping urban heat islands in Pune using Landsat 8 thermal imagery

Abstract: Rapid urbanisation has raised land surface temperatures in Indian cities. We analyse Landsat 8 thermal bands from 2014 to 2023 and find that built-up areas are on average 4.2 degrees Celsius warmer than vegetated areas. Green cover and water bodies significantly reduce local temperatures. We recommend urban greening policies.

Submitted to the National Conference on Climate and Cities, track: remote sensing.
""")
D("res_microplastic", "Downloads/scan_0044.pdf", """
Occurrence of microplastics in surface water of the Mula-Mutha river
Abstract - We sampled surface water at 12 sites and quantified microplastic particles using FTIR spectroscopy.
Concentrations ranged from 120 to 980 particles per cubic metre, highest downstream of sewage outfalls.
Fibres and fragments of polyethylene and polypropylene dominated. Implications for freshwater ecology are discussed.
""")
D("res_perovskite", "Documents/thesis_ch1.docx", """
Chapter 1 - Introduction

Perovskite solar cells have reached power conversion efficiencies above 25 percent in little more than a decade, rivalling silicon photovoltaics. However, their long-term stability under heat, moisture and light remains the main obstacle to commercialisation.

This thesis investigates encapsulation layers and additive engineering to improve the operational lifetime of methylammonium-free perovskite cells.

Chapter 2 reviews the literature on degradation mechanisms; Chapter 3 describes device fabrication.
""")

# ---------------------------------------------------------------- F. recipes
D("rec_curry", "Documents/grandma.txt", """
Aaji's chicken curry (serves 4)

Ingredients: 1 kg chicken, 3 onions finely chopped, 2 tomatoes, ginger garlic paste 2 tbsp, 1 tsp turmeric, 2 tsp red chilli powder, 1 tbsp garam masala, fresh coriander, oil, salt.

Method: Heat oil, fry onions till golden brown. Add ginger garlic paste and cook for a minute. Add tomatoes and spices and cook until oil separates. Add chicken, stir well, cover and cook on low flame for 25 minutes. Garnish with coriander and serve hot with rice or chapati.
""")
D("rec_cookies", "Documents/Document (1).docx", """
Chocolate chip cookies

Ingredients: 225 g butter, 200 g brown sugar, 100 g white sugar, 2 eggs, 1 tsp vanilla, 280 g flour, 1 tsp baking soda, half tsp salt, 300 g chocolate chips.

Cream the butter and sugars, beat in the eggs and vanilla. Mix in the flour, baking soda and salt, then fold in the chocolate chips.

Scoop onto a lined tray and bake at 180 C for 10-12 minutes until the edges are golden. Cool on a wire rack.
""")
D("rec_pasta", "Documents/dinner_ideas.md", """
# Dinner ideas

## Spaghetti carbonara
Boil spaghetti. Fry pancetta until crisp. Whisk eggs with grated pecorino and black pepper, toss with the hot pasta off the heat so the sauce turns creamy.

## Veggie stir fry
Broccoli, bell peppers, carrots and tofu in a hot wok with soy sauce, garlic and a little honey. Serve over jasmine rice.
""")
D("rec_banana", "Downloads/scan_0019.pdf", """
Banana bread
3 ripe bananas, mashed. 1/3 cup melted butter. 3/4 cup sugar. 1 egg, beaten. 1 tsp vanilla.
1 tsp baking soda, pinch of salt, 1 1/2 cups all-purpose flour.
Mix the butter into the mashed bananas, then the sugar, egg and vanilla. Stir in baking soda, salt and flour.
Pour into a greased loaf pan and bake at 175 C for about 60 minutes.
""")
D("rec_mealplan", "Documents/week_plan.xlsx", [
    ["Day", "Breakfast", "Lunch", "Dinner"],
    ["Monday", "Poha", "Dal rice", "Chicken curry"],
    ["Tuesday", "Oats", "Rajma chawal", "Veggie stir fry"],
    ["Wednesday", "Upma", "Chole with roti", "Spaghetti carbonara"],
    ["Thursday", "Idli", "Paneer wrap", "Khichdi"],
    ["Friday", "Paratha", "Curd rice", "Pizza night"],
])

# ---------------------------------------------------------------- G. travel
D("trav_flight", "Downloads/e-ticket_7HQK2M.pdf", """
ELECTRONIC TICKET ITINERARY RECEIPT
Booking reference (PNR): 7HQK2M   Passenger: MEHTA/AARAV MR
Flight SQ 423 Mumbai (BOM) to Singapore (SIN), 12 Nov 2024, departs 23:55, arrives 07:50 +1, Economy.
Baggage: 30 kg checked, 7 kg cabin. Seat 41K.
Check-in closes 60 minutes before departure. Carry a valid passport and visa.
""")
D("trav_japan", "Documents/itinerary.docx", """
Japan trip - 5 days in April

Day 1: Arrive at Narita, train to Shinjuku, check in at the hotel. Evening walk in Shibuya.

Day 2: Tokyo - Senso-ji temple in Asakusa, teamLab Planets, Tokyo Skytree.

Day 3: Shinkansen to Kyoto (book JR pass). Fushimi Inari shrine, Gion in the evening.

Day 4: Arashiyama bamboo grove, Kinkaku-ji golden pavilion. Day trip option to Nara deer park.

Day 5: Osaka - Dotonbori street food, fly home from Kansai airport.
""")
D("trav_train", "Downloads/scan_0003.pdf", """
INDIAN RAILWAYS - ELECTRONIC RESERVATION SLIP
PNR 0000000000   Train 12124 Deccan Queen   From PUNE JN (PUNE) to CSMT MUMBAI
Date of journey 18-Jan-2024, departure 07:15. Class: Chair Car (CC). Coach D3 Seat 45.
Passenger: AARAV MEHTA, age 22, M. Fare INR 255. Status: CNF.
""")
D("trav_hotel", "Documents/booking_confirmation.txt", """
Booking confirmation - Casa Alfama Guesthouse, Lisbon

Confirmation number 4021-8873. Guest: Aarav Mehta.
Check-in: Monday 9 September 2024 from 15:00. Check-out: Friday 13 September 2024 by 11:00.
Room: double room with balcony, breakfast included. 4 nights, total EUR 412.
Free cancellation until 2 September. Address: Rua dos Remedios 22, Lisbon, Portugal.
""")
D("trav_goa", "Documents/trip.md", """
# Goa road trip (Dec)

Route: Pune - Kolhapur - Amboli ghat - North Goa (approx 450 km, 9 hours).
Stay: 3 nights in Anjuna homestay. Rent scooters there.

Packing list: sunscreen, swimwear, chargers, power bank, driving licence, first aid kit.

Plan: Day 1 Baga and Calangute beach, Day 2 Fort Aguada and Chapora, Day 3 Old Goa churches and Panjim.
""")
D("trav_insurance", "Downloads/policy_TI88231.pdf", """
Overseas travel insurance - policy schedule
Policy number TI-88231. Insured: Aarav Mehta. Trip: India to Singapore, 12 Nov - 19 Nov 2024.
Cover: medical expenses up to USD 50,000, trip cancellation, loss of passport, baggage delay.
Emergency assistance helpline available 24x7. Premium paid INR 1,180.
""")

# ---------------------------------------------------------------- H. device manuals
D("man_washer", "Downloads/manual.pdf", """
FRONT LOAD WASHING MACHINE - USER MANUAL (Model WF70-1206)
Safety instructions: unplug the machine before cleaning. Do not wash items soaked in petrol or solvents.
Installation: remove the transit bolts at the back before first use. Level the machine with the adjustable feet.
Wash programs: Cotton 60, Synthetics 40, Quick wash 15 minutes, Wool, Baby care, Drum clean.
Detergent drawer: compartment I prewash, II main wash, softener in the middle.
Error codes:
E1 - water inlet problem: check that the tap is open and the inlet hose is not kinked.
E2 - drain problem: clean the drain pump filter behind the small door at the bottom right.
E3 - door not locked: close the door firmly until it clicks.
E4 - unbalanced load: redistribute the laundry and restart.
Maintenance: clean the filter every month and run the drum clean program.
Warranty: 2 years comprehensive, 10 years on the motor.
""")
D("man_router", "Documents/Document (2).docx", """
Quick installation guide - AC1200 dual band wireless router

Connect the router's WAN port to your broadband modem with the supplied Ethernet cable and power on the router.

Connect your computer to the Wi-Fi network printed on the label at the bottom (default SSID and password).

Open a browser and go to 192.168.0.1. Log in with the default admin password and follow the quick setup wizard: choose your internet connection type (PPPoE / dynamic IP), set a new Wi-Fi name and password for the 2.4 GHz and 5 GHz bands.

To reset to factory settings, hold the reset button for 10 seconds until the LEDs blink.
""")
D("man_microwave", "Downloads/scan_0050.pdf", """
Microwave oven 25L convection - instruction manual
Do not operate the oven empty. Use only microwave-safe containers; never use metal in microwave mode.
Auto cook menus: 1 popcorn, 2 reheat, 3 pizza, 4 potato, 5 beverage. Press start to confirm.
Defrost by weight: select defrost, enter the weight, press start. Turn the food halfway.
Child lock: press and hold stop for 3 seconds. Cleaning: wipe the cavity with a damp cloth.
""")
D("man_headphones", "Documents/QuickStart.txt", """
WH-CH720N Quick Start

Charging: connect the USB-C cable; the indicator turns off when fully charged (about 3.5 hours).

Pairing: hold the power button for 7 seconds until the indicator flashes blue and red. On your phone, open Bluetooth settings and select WH-CH720N.

Noise cancelling: press the NC/AMB button to switch between noise cancelling and ambient sound.

Reset: hold the power and NC buttons together for 7 seconds.
""")
D("man_printer", "Documents/printer_troubleshooting.md", """
# Printer troubleshooting (office HP LaserJet)

## Paper jam
1. Turn off the printer and open the rear access door.
2. Pull the jammed paper out slowly with both hands so it does not tear.
3. Check the toner cartridge area for scraps of paper.
4. Close the doors and turn the printer back on.

## Faded prints
Shake the toner cartridge gently or replace it.

## Printer offline
Restart the print spooler service and check the Wi-Fi connection.
""")

# ---------------------------------------------------------------- I. medical
D("med_cbc", "Downloads/report_2024.pdf", """
SUNRISE DIAGNOSTICS - LABORATORY REPORT
Patient: Aarav Mehta   Age/Sex: 22/M   Sample collected: 03-Feb-2024 08:10
Test: Complete Blood Count (CBC)
Haemoglobin 14.2 g/dL (13.0 - 17.0)
Total WBC count 7,800 /cumm (4,000 - 11,000)
Platelet count 2.6 lakh /cumm (1.5 - 4.5)
RBC count 4.9 million/cumm. Hematocrit 43 percent.
Impression: values within normal limits. Report verified by Dr. S. Patil, MD Pathology.
""")
D("med_lipid", "Downloads/scan_0022.pdf", """
Lipid profile (fasting)
Total cholesterol 212 mg/dL (desirable < 200) - borderline high
HDL cholesterol 41 mg/dL, LDL cholesterol 138 mg/dL, triglycerides 165 mg/dL
Vitamin D 18 ng/mL - deficient. Fasting blood sugar 92 mg/dL.
Advice: repeat after 3 months, diet and exercise. Sunrise Diagnostics, Pune.
""")
D("med_discharge", "Documents/discharge_summary.docx", """
City Care Hospital - Discharge Summary

Patient: Aarav Mehta, 21 years. Date of admission: 10 Oct 2023. Date of discharge: 13 Oct 2023.

Diagnosis: acute appendicitis. Procedure: laparoscopic appendectomy under general anaesthesia, uneventful.

Medication on discharge: antibiotics for 5 days, paracetamol as needed. Review in the surgical OPD after one week for suture removal. Avoid lifting heavy weights for 4 weeks.
""")
D("med_rx", "Documents/prescription.txt", """
Dr. Neha Deshpande, MBBS MD (General Medicine) - Reg. No. 74512
Date: 05/02/2024   Patient: Aarav V., 22 M
Complaints: fever and sore throat for 3 days.
Rx:
1. Tab. Azithromycin 500 mg - once daily for 3 days
2. Tab. Paracetamol 650 mg - SOS for fever
3. Warm saline gargles three times a day
Follow up if fever persists beyond 3 days.
""")
D("med_vaccine", "Downloads/certificate_vax.pdf", """
Certificate for COVID-19 vaccination
Beneficiary name: Aarav Mehta. Age 22. Gender: male.
Vaccine name: COVISHIELD. Dose 1: 14 June 2021. Dose 2: 06 Sept 2021. Precaution dose: 20 Feb 2022.
Vaccinated at: PHC Kothrud, Pune. Fully vaccinated.
""")
D("vet_report", "Documents/vet_visit_max.pdf", """
Happy Paws Veterinary Clinic
Patient: Max (Labrador retriever, 4 years). Owner: Aarav Mehta.
Reason for visit: annual check-up and rabies booster. Weight 31 kg. Mild ear infection treated with drops.
Next deworming due in 3 months.
""")

# ---------------------------------------------------------------- J. code READMEs
D("readme_csv", "Projects/csv2json/README.md", """
# csv2json

A tiny command line tool that converts CSV files to JSON.

## Install
pip install -e .

## Usage
csv2json input.csv --output out.json --indent 2

Options: --delimiter, --encoding, --array (emit a JSON array instead of JSON lines).

## License
MIT
""")
D("readme_weather", "Projects/skycast/README (2).md", """
# SkyCast - weather app

React Native app showing the current weather and a 7-day forecast using the OpenWeatherMap API.

## Getting started
npm install, then npx expo start. Put your API key in a .env file as OWM_KEY.

## Features
Location search, hourly chart, dark mode, offline cache of the last forecast.
""")
D("readme_plant", "Projects/plantbot/readme.txt", """
PlantBot - automatic plant watering system

Hardware: Arduino Uno, capacitive soil moisture sensor, 5V relay module, small submersible pump, 9V battery.

How it works: the sensor reading is checked every 10 minutes; when the soil is drier than the threshold the relay switches the pump on for 3 seconds.

Wiring: sensor to A0, relay to pin 7. Upload plantbot.ino with the Arduino IDE. Calibrate the threshold in config.h.
""")
D("readme_api", "Projects/orders-service/docs.md", """
# Orders service - REST API

Base URL: /api/v1. All requests need a bearer token in the Authorization header.

## Endpoints
GET /orders - list orders (paginated, ?page=&size=)
POST /orders - create an order (JSON body: customer_id, items)
GET /orders/{id} - fetch one order
PATCH /orders/{id}/status - update the order status
DELETE /orders/{id} - cancel an order

Errors are returned as JSON with code and message fields. Rate limit: 100 requests per minute.
""")

# ---------------------------------------------------------------- K. meeting minutes
D("min_sprint", "Documents/minutes_0412.docx", """
Team sync - minutes, 12 April

Attendees: Aarav, Kunal, Sneha, Arjun (scrum master).

Sprint planning: we committed to 21 story points for sprint 14. Priorities are the CSV export bug, the login page redesign and the search API.

Decisions: release moved to 26 April; code freeze on 24 April. Daily stand-up stays at 10:00.

Action items: Kunal to fix the export bug, Sneha to share the Figma designs, Aarav to write API tests.
""")
D("min_society", "Documents/Document (4).docx", """
Shanti Apartments Cooperative Housing Society - minutes of the general body meeting held on 3 March

Agenda 1: Cleaning of the overhead water tank - approved, contractor to be paid INR 6,500, to be done on 10 March.

Agenda 2: Maintenance charges increased from INR 2,000 to INR 2,300 per flat per month from April.

Agenda 3: Lift repair and CCTV installation - quotations to be collected by the secretary.

Meeting ended with a vote of thanks by the chairman.
""")
D("min_client", "Documents/notes_meeting.txt", """
Client call - Northwind Retail - project kickoff

Present: Ms. Fernandes (client), Aarav, Arjun.
Scope: weekly sales dashboard for 40 stores, data from their POS export.
Timeline: prototype in 3 weeks, go-live by end of quarter.
Open questions: access to historical data, single sign-on, who owns data quality.
Next steps: send a proposal and estimate by Friday.
""")
D("min_club", "Documents/MoM_board.pdf", """
Coding club - core committee meeting minutes
Discussed the budget for the annual tech fest: INR 1.2 lakh requested from the college.
Hackathon on 15-16 Feb; sponsors to be approached by the marketing team.
Decided to hold weekly DSA practice sessions every Wednesday.
""")

# ---------------------------------------------------------------- L. tax forms
D("tax_form16", "Downloads/Form16_2023-24.pdf", """
FORM NO. 16 - Certificate under section 203 of the Income-tax Act, 1961 for tax deducted at source on salary
Part A: Name and address of the employer: Kestrel Analytics LLP, Pune. TAN PNEK12345F.
Employee: Aarav Mehta, PAN XXXXX0000X. Assessment year 2024-25.
Total amount of tax deducted and deposited: INR 12,480.
Part B: Gross salary INR 4,20,000; deductions under chapter VI-A section 80C INR 50,000; taxable income INR 3,20,000.
""")
D("tax_w2", "Downloads/scan_0060.pdf", """
Form W-2 Wage and Tax Statement 2023
Employer identification number 84-2231190. Employer: Finvolt Inc, San Jose CA.
Box 1 Wages, tips, other compensation 18,400.00. Box 2 Federal income tax withheld 1,950.00.
Box 3 Social security wages 18,400.00. Box 4 Social security tax withheld 1,140.80.
Copy B - to be filed with employee's federal tax return.
""")
D("tax_calc", "Documents/tax_calc.xlsx", [
    ["Income tax computation", "FY 2023-24"],
    ["Gross salary", 420000],
    ["Standard deduction", 50000],
    ["Section 80C (PPF, ELSS)", 50000],
    ["Section 80D health insurance", 12000],
    ["Taxable income", 308000],
    ["Tax payable (old regime)", 2900],
    ["TDS already deducted", 12480],
    ["Refund due", 9580],
])
D("tax_itr", "Downloads/ITR_ack.pdf", """
INDIAN INCOME TAX RETURN ACKNOWLEDGEMENT - ITR-1 SAHAJ
Assessment year 2024-25. PAN XXXXX0000X. Name: AARAV MEHTA.
Date of filing: 28-Jul-2024. Acknowledgement number 482917360280724.
Total income 3,08,000. Refund 9,580. Verified using Aadhaar OTP.
""")
D("tax_property", "Documents/Document (6).docx", """
Pune Municipal Corporation - property tax payment receipt

Property ID 13/02/1150/004, Shanti Apartments, Kothrud. Owner: Suresh Kulkarni.

Property tax for financial year 2023-24 paid: INR 7,842. Receipt number PMC-PT-99812.

Rebate of 10 percent applied for payment before 31 May.
""")

# ---------------------------------------------------------------- M. other / distractors
D("bank_stmt", "Downloads/acct_statement_jan.xlsx", [
    ["Date", "Narration", "Withdrawal", "Deposit", "Balance"],
    ["02-01-2024", "SALARY KESTREL ANALYTICS", "", 35000, 61200],
    ["05-01-2024", "UPI SURESH KULKARNI RENT", 18000, "", 43200],
    ["09-01-2024", "UPI FRESH MART", 1088, "", 42112],
    ["15-01-2024", "NEFT SIP MUTUAL FUND", 5000, "", 37112],
    ["20-01-2024", "JIO POSTPAID BILL", 590, "", 36522],
])
D("dist_poem", "Documents/poem.txt", """
Autumn evening

The maple leaves are falling slow,
the river hums a song of gold,
and in the hush of fading light
the swallows gather, tired and old.
""")
D("dist_shopping", "Desktop stuff/list.txt", """
buy: detergent, toothpaste, AA batteries, light bulb (9W), notebook, birthday card for Aai, phone screen guard
""")
D("dist_todo", "Desktop stuff/todo.md", """
# TODO
- [ ] renew gym membership
- [ ] call the plumber about the leaking tap
- [x] submit assignment
- [ ] backup photos to the external drive
- [ ] return library books by Friday
""")
D("dist_log", "Desktop stuff/app_log.txt", """
2024-03-01 10:22:11 INFO server started on port 8080
2024-03-01 10:22:15 WARN cache miss for key user:1182
2024-03-01 10:23:02 ERROR timeout while connecting to redis at 127.0.0.1:6379
2024-03-01 10:23:05 INFO retrying connection (attempt 2)
2024-03-01 10:23:06 INFO connected
""")
D("dist_story", "Documents/chapter_one.docx", """
The rain had not stopped for three days when Meera finally opened the old trunk in her grandfather's attic.

Inside, wrapped in brittle newspaper, lay a brass compass that still pointed stubbornly east, no matter which way she turned it.

She did not know yet that it would lead her across the mountains to a village that appeared on no map.
""")
D("dist_workout", "Documents/gym.xlsx", [
    ["Day", "Exercise", "Sets", "Reps"],
    ["Mon", "Bench press", 4, 8], ["Mon", "Incline dumbbell press", 3, 10],
    ["Wed", "Squats", 4, 8], ["Wed", "Deadlift", 3, 5],
    ["Fri", "Pull ups", 4, 8], ["Fri", "Barbell row", 3, 10],
])
D("dist_books", "Documents/to_read.md", """
# Books to read
1. Sapiens - Yuval Noah Harari
2. The Pragmatic Programmer
3. Atomic Habits
4. The Three-Body Problem
5. Wings of Fire - A. P. J. Abdul Kalam
""")
D("dist_cricket", "Downloads/scores.csv", """
match,team1,team2,winner,margin
1,MI,CSK,CSK,6 wickets
2,RCB,KKR,KKR,21 runs
3,DC,PBKS,PBKS,4 wickets
4,SRH,RR,RR,12 runs
""")
D("dist_wifi", "Desktop stuff/wifi.txt", """
Home wifi: network name Mehta_5G. Guest network: Mehta_Guest. Router admin page 192.168.0.1.
""")
D("dist_speech", "Documents/farewell_speech.docx", """
Good evening everyone. Today we say goodbye to our seniors, who taught us that every bug is a lesson and every deadline is negotiable.

Thank you for the late night debugging sessions, the canteen samosas and the endless encouragement. We will miss you.
""")
D("dist_quotes", "Downloads/Document (9).txt", """
Motivational quotes
"The best time to plant a tree was 20 years ago. The second best time is now."
"Simplicity is the soul of efficiency."
"First, solve the problem. Then, write the code."
""")
D("dist_slides_club", "Documents/club_intro.pptx", [
    ("Welcome to the coding club", "Weekly contests, workshops and hackathons"),
    ("What we do", "Competitive programming, open source, web development"),
    ("Join us", "Wednesdays 5 pm, lab 3"),
])
D("dist_minutes_template", "Downloads/template.docx", """
[Company name]

[Date]

[Recipient name and address]

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
""")
D("dist_netflix", "Desktop stuff/watchlist.txt", """
watchlist: Dark, Breaking Bad, Panchayat, The Office, Kota Factory, Stranger Things
""")
D("dist_budget", "Documents/budget_2024.xlsx", [
    ["Category", "Jan", "Feb", "Mar"],
    ["Rent share", 6000, 6000, 6000],
    ["Food", 7000, 6500, 7200],
    ["Transport", 1500, 1800, 1200],
    ["Phone", 590, 590, 590],
    ["Entertainment", 1200, 900, 1500],
])

# ---------------------------------------------------------------- long documents (chunking matters)
D("handbook", "Downloads/Employee_Handbook_2024.pdf", """
KESTREL ANALYTICS LLP - EMPLOYEE HANDBOOK 2024

1. Welcome. This handbook explains the policies that apply to all employees and interns. Please read it carefully and keep it for reference. Policies may be updated from time to time; the latest version is always on the intranet.

2. Working hours. Core hours are 10:00 to 16:00. Employees may choose their start time between 8:00 and 11:00. Remote work is allowed two days a week with the approval of your manager. Attendance is recorded in the HR portal.

3. Leave policy. Every employee earns 24 days of paid annual leave per calendar year, credited monthly. In addition there are 12 days of sick leave and 10 public holidays. Unused annual leave up to 10 days can be carried forward to the next year. Leave must be applied for in the HR portal at least one week in advance, except for sick leave. Maternity leave of 26 weeks and paternity leave of 2 weeks are provided as per law.

4. Travel policy. All business travel must be approved by your manager. Flight tickets and train tickets are booked through the travel desk; economy class for flights under 6 hours. Hotel bookings are limited to INR 6,000 per night in metro cities. A daily allowance of INR 1,500 covers meals. Submit boarding passes, hotel invoices and receipts within 15 days of returning to claim reimbursement. Overseas travel requires travel insurance, which the company buys.

5. Medical insurance. Employees are covered by a group health insurance policy with a sum insured of INR 5 lakh, including spouse and two children. Hospitalisation is cashless at network hospitals. An annual health check-up including blood tests and a lipid profile is offered free of charge. Keep your discharge summary and prescriptions to claim reimbursement for non-network hospitals.

6. Income tax and payroll. Salary is paid on the last working day of each month. Tax is deducted at source (TDS) according to your declared investments. Submit investment proofs under section 80C (PPF, ELSS, life insurance) and section 80D by 31 January. Form 16 is issued by 15 June every year. To claim house rent allowance (HRA) exemption, submit monthly rent receipts; if annual rent exceeds INR 1 lakh, the landlord's PAN and a copy of the rent agreement or lease are mandatory.

7. Equipment and IT. Each employee receives a company laptop. Do not install unlicensed software. Report a lost or stolen laptop to the IT helpdesk within 24 hours. Office Wi-Fi credentials are personal and must not be shared. Printers are on every floor; report paper jams or toner issues to the facilities team.

8. Meetings and communication. Keep meetings short and share an agenda in advance. Minutes and action items should be posted in the team channel within one day. Sprint planning happens every second Monday for engineering teams.

9. Code of conduct. We expect honesty, respect and professionalism. Harassment of any kind is not tolerated. Conflicts of interest must be disclosed to HR. Confidential client data must never leave company systems.

10. Exit process. The notice period is 60 days for employees and 15 days for interns. Return all company property on your last day. The full and final settlement is processed within 45 days.
""")
D("ml_full", "College/Sem5/CS354_compiled.pdf", """
CS 354 Introduction to Machine Learning - compiled class notes (Prof. A. Rao), weeks 1 to 10

Week 1 - What is machine learning? A program learns from experience E with respect to task T and performance measure P if its performance at T improves with E. Supervised learning uses labelled examples (classification and regression); unsupervised learning finds structure in unlabelled data (clustering, dimensionality reduction); reinforcement learning learns from rewards.

Week 2 - k-nearest neighbours. Classify a point by majority vote of its k closest training points. Choice of distance metric (Euclidean, Manhattan, cosine) and of k; small k overfits, large k underfits. Curse of dimensionality: distances become less informative in high dimensions.

Week 3 - Linear regression and gradient descent, see the separate notes. Mean squared error, learning rate, feature scaling and regularisation.

Week 4 - Logistic regression. The sigmoid function maps scores to probabilities. Cross-entropy loss, decision boundary, one-vs-rest for multiple classes. Evaluation with accuracy, precision, recall and F1 score; ROC curves and AUC.

Week 5 - Decision trees and ensembles. Gini impurity, information gain, pruning. Random forests reduce variance by bagging; gradient boosting reduces bias by fitting residuals.

Week 6 - Naive Bayes. Bayes' theorem with the conditional independence assumption; Laplace smoothing; works well for text classification such as spam filtering.

Week 7 - Support vector machines. Maximum-margin classifier, support vectors, soft margin with the C parameter, the kernel trick (polynomial and RBF kernels).

Week 8 - Unsupervised learning. k-means clustering: assign points to the nearest centroid and recompute centroids until convergence; choose k with the elbow method. Hierarchical clustering and dendrograms.

Week 9 - Principal component analysis. Project data onto the directions of maximum variance (eigenvectors of the covariance matrix); explained variance ratio; used for visualisation and compression.

Week 10 - Bias-variance trade-off and model selection. Training, validation and test splits; k-fold cross-validation; learning curves; hyperparameter tuning with grid search. Neural networks and backpropagation are covered in lecture 11.

Exam: the final exam covers weeks 1 to 12; bring a calculator. Assignments contribute 30 percent of the grade.
""")
