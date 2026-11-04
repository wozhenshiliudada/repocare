import http from 'node:http';

// Test-only local SSE model. Real Pi sessions, tool parsing and transport are exercised.
export async function startMock({scenario = 'fix', unauthorized = false, failure = false, comparisonCases = null, onRequest = null, script = null} = {}) {
  const requests = [];
  const server = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    if(onRequest)await onRequest(body,requests.length); // Bounded test-only race injection.
    if (script) {
      const reply = script(body, requests.length);
      const delta = reply.name ? {role:'assistant', tool_calls:[{index:0,id:`call_${requests.length}`,
        type:'function',function:{name:reply.name,arguments:reply.rawArguments ?? JSON.stringify(reply.args)}}]} :
        {role:'assistant',content:reply.content ?? 'finished'};
      res.writeHead(200, {'content-type':'text/event-stream'});
      res.end('data: '+JSON.stringify({id:'mock',object:'chat.completion.chunk',created:1,model:body.model,
        choices:[{index:0,delta,finish_reason:reply.finish ?? (reply.name?'tool_calls':'stop')}]})+'\n\ndata: [DONE]\n\n');
      return;
    }
    if(failure==='stall') {
      res.writeHead(200,{'content-type':'text/event-stream'});res.flushHeaders();return;
    }
    if(failure==='truncated') {
      res.writeHead(200,{'content-type':'text/event-stream'});
      res.end('data: '+JSON.stringify({id:'mock',object:'chat.completion.chunk',created:1,model:body.model,
        choices:[{index:0,delta:{role:'assistant',content:'untrusted provider text sk-syntheticNotARealKey123456'},finish_reason:null}]})+'\n\n');
      return;
    }
    if(failure==='no_plan') {
      res.writeHead(200,{'content-type':'text/event-stream'});
      res.end('data: '+JSON.stringify({id:'mock',object:'chat.completion.chunk',created:1,model:body.model,
        choices:[{index:0,delta:{role:'assistant',content:'no structured plan'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');return;
    }
    if (failure) {
      res.writeHead(500, {'content-type': 'application/json'});
      res.end(JSON.stringify({error: {message: 'synthetic replay failure Bearer syntheticNotARealKey123456'}}));
      return;
    }
    // Persistent Pi now includes earlier accepted proposals. Only this turn may finish this response.
    const currentUser = body.messages.findLastIndex(message => message.role === 'user');
    const current = body.messages.slice(currentUser + 1);
    const read = current.some(message => message.role === 'tool' && message.content?.includes('before_sha256'));
    const submitted = current.some(message => message.role === 'tool' && message.content?.includes('accepted'));
    const user = body.messages[currentUser];
    const context = JSON.parse(typeof user.content === 'string' ? user.content : user.content[0].text);
    const comparison = comparisonCases?.find(c => context.issue.startsWith('comparison:' + c.id + ': '));
    const replacement = scenario === 'retry_fix' ? (context.feedback ? 'a + b' : 'a * b')
      : {fix: 'a + b', broken: 'a * b', regression: 'abs(a + b)'}[scenario];
    let delta, finish;
    if (submitted) {
      delta = {role: 'assistant', content: 'Repair proposed for review.'};
      finish = 'stop';
    } else {
      const name = read ? 'propose_repair' : 'read_source';
      const sourceMessage = current.find(message => message.role === 'tool' && message.content?.includes('before_sha256'));
      const source = sourceMessage ? JSON.parse(sourceMessage.content).content : '';
      const args = read ? {diagnosis: comparison ? 'Public scripted fixture response: ' + comparison.id : 'addition uses subtraction', risk: 'low',
        edits: [{path: unauthorized ? '../escape.py' : comparison ? 'logic.py' : 'calculator.py',
          replacements: [{oldText: source,
            newText: comparison ? (context.feedback && comparison.later !== null ? comparison.later : comparison.first)
                               : `def add(a, b):\n    return ${replacement}\n`}]}]} : {path: comparison ? 'logic.py' : 'calculator.py'};
      if (scenario === 'local_fix') {
        if (!read) args.query = 'def add';
        else args.edits[0].replacements = [{oldText: 'return a - b', newText: 'return a + b'}];
      }
      if (scenario === 'fragment' && read)
        args.edits = [{path:'calculator.py',content:'    return a + b\n'}];
      delta = {role: 'assistant', tool_calls: [{index: 0, id: `call_${requests.length}`,
        type: 'function', function: {name, arguments: JSON.stringify(args)}}]};
      finish = 'tool_calls';
    }
    res.writeHead(200, {'content-type': 'text/event-stream'});
    const chunk = (delta, finish_reason) => ({id: 'mock', object: 'chat.completion.chunk',
      created: 1, model: body.model, choices: [{index: 0, delta, finish_reason}]});
    res.write(`data: ${JSON.stringify(chunk(delta, null))}\n\n`);
    res.write(`data: ${JSON.stringify(chunk({}, finish))}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {url: `http://127.0.0.1:${server.address().port}/v1`, requests,
    close: () => new Promise(resolve => {server.closeAllConnections(); server.close(resolve);})};
}
