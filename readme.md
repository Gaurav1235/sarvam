What will make this bot successful?

Ability to search for the available restaurants by filters instead of manually searching for the desirable one. 
Ability to book the desirable restaurant just by giving it a command instead of manually adding all the fields.
Ability to talk like a human - telling about the avaiable seats and menus of a particular resturant or whole. 

State Transition Diagram:
https://docs.google.com/document/d/1V9_Bgj9mRXLfNt2ooq4lNXEMui7VHsrxZtI_4ta8nOU/edit?usp=sharing

Bot features: 

What is the flow the end the customer cares about?

Ability to search for the desirable restaurant easily. Booking the restaurant easily. 

Ask naturally
→ “Find me a rooftop restaurant in Delhi that serves sushi for four people at 8 PM.”
(No forms, just conversation.)

Get instant suggestions
→ Bot shows 2–3 matching restaurants with names, cuisines, ratings, and vibes.
(Quick, clear, helpful — not overwhelming.)

Check availability
→ Bot automatically checks open tables at the requested time.

Book instantly
→ Bot confirms: “Sakura Sky Lounge is available at 8 PM. Should I book it for you?”
→ Customer says “Yes.”
→ Bot confirms booking and provides a reservation code.

Modify or cancel easily
→ “Cancel my reservation for Sakura Sky Lounge tonight.”
→ Bot confirms cancellation politely.

See all bookings
→ “Show my upcoming reservations.”
→ Bot lists them clearly.

Key specifications that the customer cares about? 

They can simply talk or type naturally (“Find me a sushi place for 4 tonight”) — no commands or forms.
Suggesting desirable recommendations
Selecting correct place and time while booking
Shows restaurant details (address, hours, seating type, rating) so users know what they’re booking.
Gives a clear “✅ Reservation confirmed” message and a booking code immediately.
Showing reservation to customer while also updating to restaurant
Ease in modifying or canceling the reservation 
Protects their name, contact info, and doesn’t misuse personal data.
Quick responses, no downtime or “try again later” frustrations.
Handles edge cases gracefully — e.g., “Sorry, that’s full. Would you like me to find another nearby rooftop sushi restaurant?”
Customer shouldnt able to book date before now.

KBs: 

Core structured database of restaurants and their details.
Real-time or cached view of each restaurant’s seating capacity by time slot.
Records of all bookings, modifications, and cancellations.
Location data to resolve user queries like “near HSR Layout, Delhi.”

Does it need Tools?
Yes, ofcourse. LLM will interact with tools based on user input. 

Which Languages: 
User can ask in any language but the output will be in english. 

Any new features asked for?
Stores user preferences for cuisine, seating, budget, previous bookings, favorites.

Color Green, Yellow, Red basis difficulty
Didnt understood this question 





# sarvam

setup instructions: 
install  python 3.11 

Before running -> install dependencies such as openai, dotenv, streamlit
and place OPENAI_API_KEY in .env file 

To run : 

streamlit run reservation.py 

OR 

you can setup virtual environment -> 
python -m venv venv
(mac) source venv/bin/activate 
pip install streamlit dotenv openai

DEMO LINK: 

https://drive.google.com/file/d/1pXDPhBJSyNgOUTZDXV76qkT-Lg1sjcjG/view?usp=sharing