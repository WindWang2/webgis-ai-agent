import { createRequire } from 'node:module';
import { afterEach, describe, expect, it } from 'vitest';

// Resolve through jsPDF, not a separate top-level sanitizer: this is the
// production optional dependency pinned by pnpm-lock.yaml. Each test gets a
// fresh instance so hooks/configuration cannot leak into other export tests.
const require = createRequire(import.meta.url);
const requireFromJsPdf = createRequire(require.resolve('jspdf'));
interface Sanitizer {
  sanitize(input: Node | string, config?: Record<string, unknown>): Node | string;
  addHook(name: string, hook: (node: Element) => void): void;
}
const createSanitizer = requireFromJsPdf('dompurify') as (window: Window) => Sanitizer;

afterEach(() => {
  document.body.replaceChildren();
});

describe('jsPDF sanitizer dependency security', () => {
  // https://github.com/advisories/GHSA-cg2g-xh64-v4q7
  it.each([
    ['$', '{danger()}'],
    ['{', '{danger()}}'],
    ['<', '%danger()%>'],
  ])('removes a split %s template expression from an attached shadow root', (start, end) => {
    const sanitizer = createSanitizer(window);
    const host = document.createElement('div');
    const shadow = host.attachShadow({ mode: 'open' });
    shadow.append(start, document.createElement('object'), end);

    sanitizer.sanitize(host, { IN_PLACE: true, SAFE_FOR_TEMPLATES: true });

    expect(shadow.querySelector('object')).toBeNull();
    expect(shadow.textContent).not.toContain(`${start}${end}`);
    expect(shadow.textContent).not.toContain('danger()');
  });

  it('preserves benign text and markup in an attached shadow root', () => {
    const sanitizer = createSanitizer(window);
    const host = document.createElement('div');
    const shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = '<p>Map <strong>legend</strong>: 10 km</p>';

    sanitizer.sanitize(host, { IN_PLACE: true, SAFE_FOR_TEMPLATES: true });

    expect(shadow.innerHTML).toBe('<p>Map <strong>legend</strong>: 10 km</p>');
  });

  // https://github.com/advisories/GHSA-p98j-92pf-mc4p
  it.each(['afterSanitizeElements', 'afterSanitizeAttributes'])(
    'neutralizes descendants removed by an %s hook',
    (hook) => {
      const sanitizer = createSanitizer(window);
      const root = document.createElement('div');
      root.innerHTML = '<section id="removed"><img src="x" onerror="danger()"></section><p>Map legend</p>';
      document.body.append(root);
      const image = root.querySelector('img')!;
      sanitizer.addHook(hook, (node) => {
        if (node.id === 'removed') node.remove();
      });

      sanitizer.sanitize(root, { IN_PLACE: true });

      expect(root.querySelector('section')).toBeNull();
      expect(image.getAttribute('onerror')).toBeNull();
      expect(root.innerHTML).toBe('<p>Map legend</p>');
    },
  );

  // https://github.com/advisories/GHSA-mrv2-3pqr-j5jr
  describe.each([
    'beforeSanitizeElements',
    'uponSanitizeElement',
    'afterSanitizeElements',
    'beforeSanitizeAttributes',
    'afterSanitizeAttributes',
  ])('%s hook', (hook) => {
    it.each(['parent', 'sibling'])('neutralizes a removed %s subtree', (target) => {
      const sanitizer = createSanitizer(window);
      const root = document.createElement('div');
      root.innerHTML = '<figure><img class="trigger" src="a"><img id="removed" src="x" onerror="danger()"></figure><p>Map legend</p>';
      document.body.append(root);
      const image = root.querySelector('#removed')!;
      sanitizer.addHook(hook, (node) => {
        if (!node.classList?.contains('trigger')) return;
        if (target === 'parent') node.parentElement!.remove();
        else node.nextElementSibling!.remove();
      });

      sanitizer.sanitize(root, { IN_PLACE: true });

      expect(root.contains(image)).toBe(false);
      expect(image.getAttribute('onerror')).toBeNull();
      expect(root.querySelector('p')?.textContent).toBe('Map legend');
    });
  });

  // https://github.com/advisories/GHSA-6688-9rhm-gjv2
  it('fails closed instead of returning a removed rawtext root for HTML reparse', () => {
    const sanitizer = createSanitizer(window);
    const root = document.createElement('style');
    root.setAttribute('onclick', 'danger()');
    root.textContent = '</style><img src="x" onerror="danger()">';
    document.body.append(root);

    expect(() => sanitizer.sanitize(root, { IN_PLACE: true })).toThrow(TypeError);
    expect(root.getAttribute('onclick')).toBeNull();
    // The security contract is fail-closed: no node is returned for reparse.
    // A caller must not ignore the exception and reuse its original raw input.
  });

  it('preserves a benign rawtext root in place', () => {
    const sanitizer = createSanitizer(window);
    const root = document.createElement('style');
    root.textContent = '.legend { color: blue; }';
    document.body.append(root);

    expect(sanitizer.sanitize(root, { IN_PLACE: true })).toBe(root);
    expect(root.textContent).toBe('.legend { color: blue; }');
    expect(root.parentNode).toBe(document.body);
  });

  // https://github.com/advisories/GHSA-c6gr-qvv4-5v3f
  it('removes SMIL href mutation in XHTML mode while retaining benign SVG', () => {
    const sanitizer = createSanitizer(window);
    const clean = sanitizer.sanitize(
      '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"><a>'
      + '<animate attributeName="href" values="x;javascript:danger()" dur="1s"/>'
      + '<text x="10" y="40">Map legend</text></a></svg>',
      {
        PARSER_MEDIA_TYPE: 'application/xhtml+xml',
        ALLOWED_TAGS: ['svg', 'a', 'animate', 'text'],
        ALLOWED_ATTR: ['attributeName', 'values', 'dur', 'x', 'y', 'width', 'height'],
      },
    );
    expect(typeof clean).toBe('string');
    const svg = new DOMParser().parseFromString(clean as string, 'image/svg+xml');

    expect(svg.querySelector('parsererror')).toBeNull();
    expect(svg.querySelector('animate')).not.toBeNull();
    expect(svg.querySelector('animate')?.getAttribute('attributeName')).toBeNull();
    expect(svg.querySelector('text')?.textContent).toBe('Map legend');
    expect(svg.documentElement.getAttribute('width')).toBe('200');
  });

  it('keeps ordinary report markup while removing scripts and event handlers', () => {
    const sanitizer = createSanitizer(window);
    const clean = sanitizer.sanitize(
      '<h2>Map report</h2><p><strong>Population:</strong> 42</p>'
      + '<img src="data:," onerror="danger()"><script>danger()</script>',
    );
    expect(clean).toBe('<h2>Map report</h2><p><strong>Population:</strong> 42</p><img src="data:,">');
  });
});
