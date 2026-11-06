import ts from 'typescript';

export interface MapAccess {
  name: string;
  line: number;
  kind: 'call' | 'access';
}

/** Scan executable map accesses, excluding text and proven native collection fields. */
export function scanMapAccesses(source: string, fileName = 'source.tsx'): MapAccess[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true);
  const accesses: MapAccess[] = [];
  // Be conservative if Map is imported or locally declared: new Map() could
  // then construct a MapLibre map rather than the native collection.
  let hasLocalMapConstructor = false;
  const findMapBinding = (node: ts.Node): void => {
    if (
      ts.isIdentifier(node) && node.text === 'Map' &&
      'name' in node.parent && node.parent.name === node
    ) hasLocalMapConstructor = true;
    ts.forEachChild(node, findMapBinding);
  };
  findMapBinding(file);

  const isNativeCollectionField = (receiver: ts.PropertyAccessExpression): boolean => {
    if (hasLocalMapConstructor || receiver.expression.kind !== ts.SyntaxKind.ThisKeyword) return false;
    let owner: ts.Node | undefined = receiver.parent;
    while (owner && !ts.isClassDeclaration(owner) && !ts.isClassExpression(owner)) owner = owner.parent;
    if (!owner) return false;
    return (owner as ts.ClassLikeDeclaration).members.some((member) =>
      ts.isPropertyDeclaration(member) && ts.isIdentifier(member.name) && member.name.text === 'map' &&
      member.initializer !== undefined && ts.isNewExpression(member.initializer) &&
      ts.isIdentifier(member.initializer.expression) && member.initializer.expression.text === 'Map',
    );
  };

  const visit = (node: ts.Node): void => {
    if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
      const receiver = node.expression;
      const isMap = ts.isIdentifier(receiver) && receiver.text === 'map';
      const isMapField = ts.isPropertyAccessExpression(receiver) && receiver.name.text === 'map' &&
        !isNativeCollectionField(receiver);
      if (isMap || isMapField) {
        const member = ts.isPropertyAccessExpression(node) ? node.name : node.argumentExpression;
        // Dynamic computed keys cannot be resolved by a syntax-only contract.
        const name = ts.isIdentifier(member) && ts.isPropertyAccessExpression(node)
          ? member.text
          : ts.isStringLiteralLike(member) ? member.text : undefined;
        if (name !== undefined) {
          accesses.push({
            name,
            line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1,
            kind: ts.isCallExpression(node.parent) && node.parent.expression === node
              ? 'call' : 'access',
          });
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return accesses;
}
