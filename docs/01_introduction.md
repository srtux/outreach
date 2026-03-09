# 01: Introduction & The ADK

Welcome to the **School Outreach Research Agent**! If you only know basic Python (like variables, loops, and functions), this guide is made just for you. We are going to explain exactly what "AI Agents" are and how this specific project works, piece by piece.

## What is this project?
Imagine you need to find the work email addresses for 100 Computer Science teachers at 100 different high schools across the country. 
Doing this manually is incredibly boring and slow. It requires:
1. Googling "High Schools in Austin, TX".
2. Finding a specific school's official website.
3. Clicking around to find their "Staff Directory" or "About Us" page.
4. Scrolling through looking for the words "Computer Science Teacher".
5. Copying their email address into a spreadsheet.

This project **automates that entire workflow** using Artificial Intelligence.

## What is an "Agent"?
In traditional programming, you write exact, strict rules. For example, you might write code saying: "Go to this exact URL, look for an HTML tag called `<div>`, and extract the text." This breaks the very second the website changes its design.

An **AI Agent**, on the other hand, works like a digital intern. Instead of exact rules, you give it a **goal** (e.g., "Find me a CS teacher in Austin") and a set of **Tools** (like a web searcher or a web scraper). 

The AI "thinks" about the problem, uses a tool, looks at the result of that tool, and makes another decision. If a school website is designed differently, the Agent just looks around until it finds the staff directory!

## What is the Google ADK?
This project is built using the **Google Agent Development Kit (ADK)**. You can think of the ADK as the engine that runs our AI intern. 

The ADK provides three critical things:
1. **The LLM (Large Language Model)**: The "brain" of the agent. We use a model called `gemini-3-flash-preview` to do the reasoning.
2. **Tools**: We can write standard Python functions and easily attach them to the brain. When the brain decides it needs to use a tool, the ADK automatically runs our Python function and feeds the answer back to the brain.
3. **Runners**: The underlying system that manages the conversation, tracks what step the agent is on, and handles complicated things like streaming data back to your screen.

## The Hallucination Problem 
Why build all this? Why not just open ChatGPT and type: *"Give me the emails of 5 principals in Dallas"*?

If you try that, it will often give you a confident answer. However, that answer is usually **hallucinated** (made up). Language models are basically extremely advanced autocorrects—they are trained to predict the next logical word. They are *not* a database of current facts. They will happily invent a plausible-sounding email address (like `john.smith@dallasisd.org`) that doesn't actually exist.

### The Solution: Tools
To prevent our agent from making things up, we don't ask it to remember facts. Instead, we give it tools to look at the continuous, live internet:
1. **Google Search Tool**: The agent can search the web to find a school's official homepage.
2. **Web Scraper Tool**: The agent can download the actual, real HTML text of that website and read it to find the real email address.

Because the LLM is reading real data extracted straight from the school's website *right now*, it doesn't need to guess. It just extracts the truth!

---
**Next up:** Let's look at how we structure these tools and how we make the code run blazingly fast in [02: Architecture and Concurrency](./02_architecture.md).
