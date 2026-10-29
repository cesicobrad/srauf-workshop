// Small helpers that build n8n workflow JSON, so the workflows in workflows/
// share the same retry, error-output and credential conventions.

import { createHash } from "node:crypto";

export const GRID_X = 240;
export const GRID_Y = 190;

function uuid(seed) {
  const h = createHash("sha256").update(seed).digest("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-4${h.slice(13, 16)}-a${h.slice(17, 20)}-${h.slice(20, 32)}`;
}

// Turn a function into Code-node source: its body only. Lets the node code be
// written (and syntax-checked) as real JavaScript in this file.
export function body(fn) {
  const s = fn.toString();
  return s.slice(s.indexOf("{") + 1, s.lastIndexOf("}")).replace(/^\n/, "").replace(/\s+$/, "");
}

export class Workflow {
  constructor({ id, name, errorWorkflow, description }) {
    Object.assign(this, { id, name, errorWorkflow, description });
    this.nodes = [];
    this.connections = {};
  }

  add(name, type, typeVersion, parameters, at, extra = {}) {
    if (this.nodes.some((n) => n.name === name)) throw new Error(`duplicate node name: ${name}`);
    const node = {
      id: uuid(`${this.id}/${name}`),
      name,
      type: type.includes(".") ? type : `n8n-nodes-base.${type}`,
      typeVersion,
      position: [Math.round(at[0] * GRID_X), Math.round(at[1] * GRID_Y)],
      parameters,
      ...extra,
    };
    this.nodes.push(node);
    return name;
  }

  link(from, to, output = 0, input = 0) {
    const c = (this.connections[from] ??= { main: [] });
    while (c.main.length <= output) c.main.push([]);
    c.main[output].push({ node: to, type: "main", index: input });
  }

  chain(...names) {
    for (let i = 0; i < names.length - 1; i++) this.link(names[i], names[i + 1]);
  }

  // ---- node helpers ----

  webhook(name, at, { path, method = "POST", respond = "responseNode" }) {
    return this.add(name, "webhook", 2.1, {
      httpMethod: method,
      path,
      responseMode: respond,
      options: { allowedOrigins: "*" },
    }, at, { webhookId: uuid(`${this.id}/${name}/webhook`) });
  }

  // Schedule trigger followed by a pause switch: SCHEDULES_ENABLED=false stops
  // timed runs (tests, maintenance) while the "Run now" webhooks keep working.
  // Returns the name of the switch, which is what the rest of the flow links to.
  schedule(name, at, rule) {
    this.add(name, "scheduleTrigger", 1.2, { rule: { interval: [rule] } }, at);
    const gate = this.if("Schedules enabled?", [at[0] + 0.6, at[1] - 0.55], "={{ $env.SCHEDULES_ENABLED !== 'false' }}");
    this.link(name, gate, 0);
    return gate;
  }

  respond(name, at, { json, html, status = 200 }) {
    const parameters = html
      ? {
          respondWith: "text",
          responseBody: html,
          options: { responseCode: status, responseHeaders: { entries: [{ name: "Content-Type", value: "text/html; charset=utf-8" }] } },
        }
      : { respondWith: "json", responseBody: json, options: { responseCode: status } };
    return this.add(name, "respondToWebhook", 1.4, parameters, at);
  }

  code(name, at, src, { mode = "runOnceForAllItems", errorOutput = false, prelude = "" } = {}) {
    const jsCode = (prelude ? prelude + "\n\n" : "") + (typeof src === "function" ? body(src) : src);
    const extra = errorOutput ? { onError: "continueErrorOutput" } : {};
    return this.add(name, "code", 2, { mode, jsCode }, at, extra);
  }

  // HTTP Request with retries. `errorOutput` adds a second output that receives
  // the failure after the last retry, so the workflow can recover instead of stopping.
  http(name, at, { method = "GET", url, headers, jsonHeaders, query, jsonBody, retry = true, errorOutput = false,
    continueOnFail = false, fullResponse = false, neverError = false, text = false, timeout = 30000, alwaysOutputData = false, notes }) {
    const parameters = { method, url, options: { timeout } };
    if (query) {
      parameters.sendQuery = true;
      parameters.queryParameters = { parameters: Object.entries(query).map(([name, value]) => ({ name, value })) };
    }
    if (headers) {
      parameters.sendHeaders = true;
      parameters.headerParameters = { parameters: Object.entries(headers).map(([name, value]) => ({ name, value })) };
    } else if (jsonHeaders) {
      parameters.sendHeaders = true;
      parameters.specifyHeaders = "json";
      parameters.jsonHeaders = jsonHeaders;
    }
    if (jsonBody) {
      parameters.sendBody = true;
      parameters.contentType = "json";
      parameters.specifyBody = "json";
      parameters.jsonBody = jsonBody;
    }
    if (fullResponse || neverError || text) {
      parameters.options.response = { response: { fullResponse, neverError, ...(text ? { responseFormat: "text" } : {}) } };
    }
    const extra = {};
    if (retry) Object.assign(extra, { retryOnFail: true, maxTries: 3, waitBetweenTries: 2000 });
    if (errorOutput) extra.onError = "continueErrorOutput";
    else if (continueOnFail) extra.onError = "continueRegularOutput";
    if (alwaysOutputData) extra.alwaysOutputData = true;
    if (notes) Object.assign(extra, { notes, notesInFlow: true });
    return this.add(name, "httpRequest", 4.2, parameters, at, extra);
  }

  // If node on a boolean expression; output 0 = true, output 1 = false.
  if(name, at, expression) {
    return this.add(name, "if", 2.2, {
      conditions: {
        options: { caseSensitive: true, leftValue: "", typeValidation: "loose", version: 2 },
        conditions: [{ id: uuid(`${this.id}/${name}/c`), leftValue: expression, rightValue: "", operator: { type: "boolean", operation: "true", singleValue: true } }],
        combinator: "and",
      },
      looseTypeValidation: true,
      options: {},
    }, at);
  }

  merge(name, at, inputs = 2) {
    return this.add(name, "merge", 3.2, { numberInputs: inputs }, at);
  }

  token(name, at, field = "token") {
    return this.add(name, "crypto", 1, { action: "generate", dataPropertyName: field, encodingType: "hex", stringLength: 40 }, at);
  }

  note(text, at, { width = 360, height = 160, color = 7 } = {}) {
    const name = `Note ${this.nodes.filter((n) => n.type.endsWith("stickyNote")).length + 1}`;
    return this.add(name, "stickyNote", 1, { content: text, width, height, color }, at);
  }

  toJSON() {
    return {
      id: this.id,
      name: this.name,
      active: true,
      isArchived: false,
      nodes: this.nodes,
      connections: this.connections,
      settings: {
        executionOrder: "v1",
        saveManualExecutions: true,
        saveDataErrorExecution: "all",
        saveDataSuccessExecution: "all",
        ...(this.errorWorkflow ? { errorWorkflow: this.errorWorkflow } : {}),
      },
      pinData: {},
      meta: { templateCredsSetupCompleted: true },
      versionId: uuid(`${this.id}/${JSON.stringify(this.nodes)}${JSON.stringify(this.connections)}`),
    };
  }
}
