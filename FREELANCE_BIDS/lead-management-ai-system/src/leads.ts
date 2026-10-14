// LeadFlow core: provider-adapter AI summaries + n8n-compatible webhook/queue
// design: one `summarizeLead` call, swappable provider. Default adapter is a
// deterministic local mock (zero keys, offline-verifiable); the real OpenAI
// adapter is behind OPENAI_API_KEY with the same response shape.

export interface Lead {
  id: string;
  name: string;
  email: string;
  company?: string;
  budget?: string;
  notes: string;
  status: "new" | "summarized" | "contacted";
  summary?: string;
  followUpSuggestions?: string[];
  receivedAt: string; // ISO
}

export interface LeadStore {
  leads: Map<string, Lead>;
  queue: string[]; // lead ids waiting for processing (n8n workflow input)
  webhookLog: { at: string; source: string; leadId: string }[];
}

let counter = 0;
export function nextId(): string {
  counter += 1;
  return `lead_${Date.now().toString(36)}_${counter}`;
}

export function createStore(): LeadStore {
  return { leads: new Map(), queue: [], webhookLog: [] };
}

/**
 * Add a lead from a source (web intake, CRM export, n8n webhook).
 * Returns the stored lead. Invalid email -> throws.
 */
export function addLead(store: LeadStore, input: Partial<Lead> & { name: string; email: string; notes: string; source?: string }): Lead {
  const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  if (!emailRe.test(input.email)) {
    throw new Error(`invalid email: ${input.email}`);
  }
  const lead: Lead = {
    id: nextId(),
    name: input.name,
    email: input.email,
    company: input.company,
    budget: input.budget,
    notes: input.notes,
    status: "new",
    receivedAt: new Date().toISOString(),
  };
  store.leads.set(lead.id, lead);
  if (input.source) {
    store.webhookLog.push({ at: lead.receivedAt, source: input.source, leadId: lead.id });
  }
  store.queue.push(lead.id);
  return lead;
}

/**
 * AI provider adapter. The mock provider extracts budget and intent from
 * the raw notes (deterministic, offline) and returns the SAME shape the
 * OpenAI adapter returns, so the rest of the app never knows which ran.
 */
export interface SummaryResult {
  summary: string;
  followUpSuggestions: string[];
  provider: string;
}

export interface Provider {
  summarize(lead: Lead): SummaryResult | Promise<SummaryResult>;
}

function mockProvider(): Provider {
  return {
    summarize(lead: Lead): SummaryResult {
      const notes = lead.notes.toLowerCase();
      const budget = lead.budget ?? (/\$\s?(\d[\d,]*)/.test(lead.notes) ? `$${lead.notes.match(/\$\s?(\d[\d,]*)/)?.[1]}` : "not stated");
      const wants = [
        /demo|show ?me|trial/.test(notes) ? "asked for a demo" : null,
        /pricing|price|cost|budget/.test(notes) ? "asked about pricing" : null,
        /integrate|api|webhook/.test(notes) ? "needs integration work" : null,
        /deadline|by (end|monday|friday|week)/.test(notes) ? "has a deadline" : null,
      ].filter((x): x is string => x !== null);
      const summary =
        `${lead.name}${lead.company ? ` at ${lead.company}` : ""} (${lead.email}). ` +
        (wants.length ? `Signals: ${wants.join(", ")}. ` : "") +
        `Budget: ${budget}. Raw notes: "${lead.notes}"`;
      const followUpSuggestions = [
        wants.includes("asked about pricing")
          ? "Send a one-page price sheet before the follow-up call."
          : "Open with the use case they described, then propose a 15-minute slot.",
        "Ask what tool they tried before and why it did not fit.",
        "Log the decision-maker vs. evaluator distinction before the next touch.",
      ];
      return { summary, followUpSuggestions, provider: "local-mock" };
    },
  };
}

export function openaiProvider(apiKey: string, baseUrl = "https://api.openai.com/v1"): Provider {
  return {
    async summarize(lead: Lead): Promise<SummaryResult> {
      const res = await fetch(`${baseUrl}/chat/completions`, {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({
          model: process.env.OPENAI_MODEL ?? "gpt-4o-mini",
          messages: [
            {
              role: "system",
              content:
                "You summarize sales leads. Reply with JSON: {summary: string, followUpSuggestions: string[]}. Summary <= 3 sentences. 2-3 follow-up suggestions.",
            },
            { role: "user", content: `Lead: ${JSON.stringify({ ...lead, summary: undefined })}` },
          ],
        }),
      });
      if (!res.ok) throw new Error(`openai ${res.status}: ${await res.text()}`);
      const data = (await res.json()) as { choices: { message: { content: string } }[] };
      const parsed = JSON.parse(data.choices[0].message.content) as { summary: string; followUpSuggestions: string[] };
      return { summary: parsed.summary, followUpSuggestions: parsed.followUpSuggestions, provider: "openai" };
    },
  };
}

export function pickProvider(): Provider {
  return process.env.OPENAI_API_KEY ? openaiProvider(process.env.OPENAI_API_KEY) : mockProvider();
}

/** n8n workflow equivalent: pop the queue and summarize each lead. */
export async function processQueue(store: LeadStore, provider?: Provider): Promise<Lead[]> {
  const p = provider ?? pickProvider();
  const done: Lead[] = [];
  while (store.queue.length) {
    const id = store.queue.shift() as string;
    const lead = store.leads.get(id);
    if (!lead) continue;
    if (lead.status !== "new") {
      continue; // idempotent: already processed
    }
    const out = await p.summarize(lead);
    lead.summary = out.summary;
    lead.followUpSuggestions = out.followUpSuggestions;
    lead.status = "summarized";
    done.push(lead);
  }
  return done;
}

export function getLead(store: LeadStore, id: string): Lead | undefined {
  return store.leads.get(id);
}
