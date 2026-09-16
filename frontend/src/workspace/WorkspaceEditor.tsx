import Editor, { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
import 'monaco-editor/esm/vs/basic-languages/css/css.contribution';
import 'monaco-editor/esm/vs/basic-languages/html/html.contribution';
import 'monaco-editor/esm/vs/basic-languages/ini/ini.contribution';
import 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution';
import 'monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution';
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution';
import 'monaco-editor/esm/vs/basic-languages/shell/shell.contribution';
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution';
import 'monaco-editor/esm/vs/basic-languages/xml/xml.contribution';
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution';

// Use the installed Monaco build instead of the loader's public CDN default. This keeps
// local and remote Gateway sessions deterministic and allows offline file inspection.
loader.config({ monaco });

// Match the workspace's surfaces and semantic accents; syntax keeps Monaco's
// language-specific rules. These colors follow style/theme.css.
monaco.editor.defineTheme('agentevolver-dark', {
  base: 'vs-dark', inherit: true, rules: [],
  colors: {
    'editor.background': '#07100e', 'editor.foreground': '#ecf7f2',
    'editorLineNumber.foreground': '#6b857a', 'editorLineNumber.activeForeground': '#63e6b5',
    'editor.selectionBackground': '#1c4939', 'editor.lineHighlightBackground': '#0e1c19',
    'editorCursor.foreground': '#63e6b5', 'editorWidget.background': '#0e1c19',
    'editorWidget.border': '#24362f', 'editorIndentGuide.background1': '#1c302a',
  },
});
monaco.editor.defineTheme('agentevolver-light', {
  base: 'vs', inherit: true, rules: [],
  colors: {
    'editor.background': '#ffffff', 'editor.foreground': '#16362b',
    'editorLineNumber.foreground': '#536e60', 'editorLineNumber.activeForeground': '#166b4e',
    'editor.selectionBackground': '#dcebe2', 'editor.lineHighlightBackground': '#f3f7f4',
    'editorCursor.foreground': '#166b4e', 'editorWidget.background': '#ffffff',
    'editorWidget.border': '#cfddd4', 'editorIndentGuide.background1': '#e9f1ec',
  },
});

export default function WorkspaceEditor({ filePath, language, content, theme }: {
  filePath: string;
  language: string;
  content: string;
  theme: 'dark' | 'light';
}) {
  return <Editor
    height="100%"
    path={filePath}
    language={language}
    value={content}
    theme={`agentevolver-${theme}`}
    options={{
      readOnly: true,
      domReadOnly: true,
      minimap: { enabled: false },
      fontSize: 12,
      lineHeight: 19,
      folding: true,
      wordWrap: 'off',
      scrollBeyondLastLine: false,
      automaticLayout: true,
      renderValidationDecorations: 'off',
    }}
  />;
}
