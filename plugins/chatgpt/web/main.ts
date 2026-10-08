import { App, applyDocumentTheme, applyHostStyleVariables } from '@modelcontextprotocol/ext-apps';
import { OpenAIExtensions } from '@openai/mcp-extensions/app';
import '@openai/mcp-extensions/app/styles.css';
import './styles.css';
import { Library } from './library.ts';

const app = new App({ name: 'Mataroa post library', version: '0.1.0' }, {}, { autoResize: true, strict: true });
// The OpenAI extension also applies the host's interaction-cursor preference.
new OpenAIExtensions(app);
const library = new Library({
  callTool: (name, args) => app.callServerTool({ name, arguments: args }),
  openLink: url => app.openLink({ url }),
});
function applyHostContext(context: ReturnType<App['getHostContext']>): void {
  if (context?.theme) applyDocumentTheme(context.theme);
  if (context?.styles?.variables) applyHostStyleVariables(context.styles.variables);
}
// Register before the handshake: the initial open_library result must render
// immediately, without an extra list_posts call or missed one-shot notification.
app.ontoolresult = result => library.receiveInitial(result);
app.ontoolcancelled = () => library.cancelled();
app.onhostcontextchanged = applyHostContext;
try {
  await app.connect(undefined, { timeout: 15000 });
  applyHostContext(app.getHostContext());
  library.ready();
} catch { library.connectionError(); }
