ONDYNA_PROFILE="""Company: ONDYNA, French importer and distributor of CRISTINA taps and shower systems.
Products: mixers, thermostatic mixers, shower columns, concealed shower sets, hand showers,
spare parts such as cartridges and diverters. Products exist in many finishes (chrome, matt black,
brushed black, brushed rose gold, old bronze, brushed metal). References look like PF60375 or PH60251.
Clients: professionals: bathroom showrooms, plumbing wholesalers and installers.
Not clients: private individuals (consumers) asking where to buy -> other.
After-sales for this company: leaks, faulty cartridges, finish defects, installation problems, warranty claims.

Examples:
- "Ci-joint notre bon de commande 4471 : 2 mitigeurs Profilo 3 sorties noir brossé." -> new_order
- "Please send 10 diverter cartridges for the concealed shower sets, PO 8812." -> new_order
- Subject "Re: Votre commande 4471": "Finalement, prenez la finition or rose brossé au lieu du chrome." -> amendment
- "Quel est votre délai pour 6 colonnes de douche thermostatiques en vieux bronze ?" -> quote_request
- "Le mitigeur posé chez notre client fuit au niveau de la cartouche, il est sous garantie." -> after_sales
- "Cartouche défectueuse sur le PH60251, merci d'en envoyer une nouvelle." -> after_sales, also_contains [new_order]
- "Bonjour, je suis un particulier, où puis-je acheter vos robinets à Lyon ?" -> other """