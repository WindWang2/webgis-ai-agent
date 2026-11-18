import ts from 'typescript';

export interface MapAccess {
  name: string;
  line: number;
  kind: 'call' | 'access';
}

/** Scan executable map accesses, excluding text and proven native collection fields. */
export function scanMapAccesses(source: string, fileName = 'source.tsx', program?: ts.Program): MapAccess[] {
  const file = program?.getSourceFile(fileName) ?? ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true);
  const checker = program?.getTypeChecker();
  const accesses: MapAccess[] = [];
  // Be conservative if Map is imported or locally declared: new Map() could
  // then construct a MapLibre map rather than the native collection.
  let hasLocalMapConstructor = false;
  let hasLocalArrayConstructor = false;
  const findMapBinding = (node: ts.Node): void => {
    if (
      ts.isIdentifier(node) &&
      'name' in node.parent && node.parent.name === node
    ) {
      if (node.text === 'Map') hasLocalMapConstructor = true;
      if (node.text === 'Array') hasLocalArrayConstructor = true;
    }
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

  const guardedArray = (receiver: ts.Expression): boolean => {
    if (hasLocalArrayConstructor) return false;
    const receiverText = receiver.getText(file);
    const matches = (test: ts.Expression): ts.CallExpression | undefined => {
      if (ts.isParenthesizedExpression(test)) return matches(test.expression);
      if (ts.isBinaryExpression(test) && test.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken)
        return matches(test.right) ?? matches(test.left);
      return ts.isCallExpression(test) && ts.isPropertyAccessExpression(test.expression)
        && ts.isIdentifier(test.expression.expression) && test.expression.expression.text === 'Array'
        && test.expression.name.text === 'isArray' && test.arguments.length === 1
        && test.arguments[0].getText(file) === receiverText ? test : undefined;
    };
    const unchangedSince = (guard: ts.CallExpression): boolean => {
      let changed = false;
      const check = (node: ts.Node): void => {
        if (changed || node.end <= guard.end || node.getStart(file) >= receiver.getStart(file)) return;
        let target: ts.Node | undefined;
        if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
          && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment) target = node.left;
        if (ts.isVariableDeclaration(node)) target = node.name;
        if (ts.isDeleteExpression(node)) target = node.expression;
        if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node))
          && (node.operator === ts.SyntaxKind.PlusPlusToken || node.operator === ts.SyntaxKind.MinusMinusToken)) target = node.operand;
        if (target && (receiverText === target.getText(file) || receiverText.startsWith(`${target.getText(file)}.`))) changed = true;
        ts.forEachChild(node, check);
      };
      check(file);
      return !changed;
    };
    let child: ts.Node = receiver;
    for (let parent = child.parent; parent; child = parent, parent = parent.parent) {
      const guard = ts.isIfStatement(parent) && parent.thenStatement === child
        ? matches(parent.expression)
        : ts.isBinaryExpression(parent) && parent.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken
          && parent.right === child ? matches(parent.left) : undefined;
      if (guard && unchangedSince(guard)) return true;
    }
    return false;
  };

  const visit = (node: ts.Node): void => {
    if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
      const receiver = node.expression;
      const isMap = ts.isIdentifier(receiver) && receiver.text === 'map';
      const isMapField = ts.isPropertyAccessExpression(receiver) && receiver.name.text === 'map' &&
        !isNativeCollectionField(receiver);
      // Only discard proven arrays. Unknown/object-typed receivers still guard
      // future MapLibre methods; never maintain a method-name escape list.
      const arrayReceiver = (isMap || isMapField) && checker?.isArrayType(checker.getNonNullableType(checker.getTypeAtLocation(receiver)));
      if ((isMap || isMapField) && !arrayReceiver && !guardedArray(receiver)) {
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
