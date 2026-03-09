# 01: Introduction & The ADK

Welcome to the **School Outreach Research Agent**! If you are new to programming or new to building AI agents, this guide is designed to help you understand exactly what this code does and how it does it.

## What is this project?
Imagine you need to find the email addresses for 100 CS teachers at 100 different high schools across the country. 
Doing this manually requires:
1. Googling "High Schools in Austin, TX"
2. Finding a school's website.
3. Clicking on their "Staff Directory" or "About Us" page.
4. Scrolling through looking for a "Computer Science Teacher".
5. Copying their email address into a spreadsheet.

This project **automates that entire workflow** using Artificial Intelligence.

## What is an "Agent"?
In traditional programming, you write exact, step-by-step instructions (e.g., "go to this exact URL, look for an HTML tag called `<div>`, and extract the text"). This breaks down the moment a website changes its layout. 

An **AI Agent**, on the other hand, is given a high-level goal (e.g., "Find me a CS teacher in Austin") and a set of **Tools**. The AI "thinks" about the problem, decides which tool to use, looks at the result of that tool, and makes another decision until the goal is met.

## What is the Google ADK?
This project is built on the **Google Agent Development Kit (ADK)**. You can think of the ADK as the engine that runs our AI Agents. 

The ADK provides a few critical things:
1. **The LLM (Large Language Model)**: The "brain" of the agent. We use `gemini-3-flash-preview` for reasoning.
2. **Tools**: We can easily attach Python functions to the brain. When the brain needs to do something (like search the web), the ADK pauses the brain, runs the Python function, and feeds the result back to the brain.
3. **Runners**: The underlying system that manages the conversation history, streams events back to us, and handles errors (like rate limits).

## Why not just ask ChatGPT/Gemini directly?
If you just open ChatGPT and type "Give me the emails of 5 principals in Dallas", it will often give you an answer. However, that answer is usually **hallucinated** (made up). Language models are trained to predict the next word; they are not databases of current facts.

To solve this, we give our agent access to live data via **Tools**:
1. **Google Search Tool**: The agent can search the live internet to find a school's official website.
2. **Web Scraper Tool**: The agent can download the actual HTML text of that website and read it to find the real email address.

Because the LLM is reading real, live data from the school's website, it does not need to guess or hallucinate.

---
**Next up:** Let's look at how we structure these tools and make things run fast in [02: Architecture and Concurrency](./02_architecture.md).
