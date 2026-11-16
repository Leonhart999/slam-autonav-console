import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { compileScript, parse } from '@vue/compiler-sfc'

// Execute the actual TypeScript module with controlled transport and clock dependencies.
export function loadSource(path, imports = {}, globals = {}) {
  const filename = new URL(path, import.meta.url)
  return evaluate(readFileSync(filename, 'utf8'), filename.pathname, imports, globals)
}

export function loadSfc(path, imports = {}, globals = {}) {
  const filename = new URL(path, import.meta.url)
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename: filename.pathname })
  return evaluate(compileScript(descriptor, { id: 'component-test' }).content, filename.pathname, imports, globals).default
}

function evaluate(source, filename, imports, globals) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(compiled, {
    module, exports: module.exports, Error, TypeError, AbortController, Headers, Response,
    URLSearchParams, Event, ArrayBuffer, Float32Array, setTimeout, clearTimeout,
    require(name) {
      if (!(name in imports)) throw new Error(`Unexpected import: ${name}`)
      return imports[name]
    },
    ...globals,
  }, { filename })
  return module.exports
}

export function mountSetup(vue, component, initial = {}) {
  component.render = () => null
  const props = vue.reactive(initial)
  let instance
  const renderer = vue.createRenderer({
    insert() {}, remove() {}, patchProp() {}, createElement: () => ({}),
    createText: () => ({}), createComment: () => ({}), setText() {}, setElementText() {},
    parentNode: () => null, nextSibling: () => null,
  })
  const app = renderer.createApp({ render: () => vue.h(component, {
    ...props, ref: component => { if (component) instance = component.$ },
  }) })
  app.mount({})
  return { props, state: instance.setupState, app }
}

export function deferred() {
  let resolve, reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

export const task = (fields = {}) => ({
  id: 'a'.repeat(32), kind: 'evaluate', target: 'run-a', state: 'queued', phase: 'queued',
  created: 1, updated: 1, finished: null, input: {}, result: null, error: null, retry_of: null, ...fields,
})
