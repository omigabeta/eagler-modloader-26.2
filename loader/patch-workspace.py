#!/usr/bin/env python3
"""Small patches to the Eaglercraft 26.2 workspace for mods (run by weave.sh; safe to run twice).

Usage: patch-workspace.py WORKSPACE
- ServiceLoader.findFirst(): registers the donor in loader/teavm-compat with Eaglercraft's JdkMethodInjector.
- Pattern.asPredicate/asMatchPredicate (Eaglercraft's donor): returned null, because the lambda it
  returned doesn't survive being copied into Pattern; now returns a plain class (AppleSkin needs it).
- glfwGetMouseButton(): added to Eaglercraft's GLFW stand-in.
- Arrays.copyOfRange (replace-donor in loader/teavm-compat): pads with zeros past the end, as the JDK
  does, instead of throwing (SnakeYAML relies on it).
- Reader/BufferedReader.transferTo and Writer.nullWriter (and nullReader, nullInputStream,
  nullOutputStream) (donors in loader/teavm-compat), Array.setInt and the other primitive
  setters, and Files.newDirectoryStream(dir, glob), added to Eaglercraft's donors for Array and Files.
- Field.getGenericType and Class.getGenericSuperclass/getGenericInterfaces/getTypeParameters
  (Eaglercraft's donors): ask the loader first, through a hook it registers at startup, so Gson and
  other libraries see LinkedHashMap<String, Foo> and that it is a Map<K, V>, rather than raw types (a map
  of settings objects otherwise loaded as plain maps, and broke with ClassCastException).
- ServerFunctionLibrary: mods' datapack functions (.mcfunction) are compiled right away instead of as
  async tasks the loader then waits on, which the browser's single-threaded server can't do (vanilla
  ships no functions, so Eaglercraft never hit this); a function that fails to compile is logged.
- WebGL2Program: a shader that fails to compile is retried with whole numbers made decimal on the lines
  the compiler complained about (desktop GLSL turns 1 into 1.0 next to a float; the browser's GLSL ES
  doesn't, and mods' shaders such as Impact Frames' rely on it). Shaders that compile are untouched.
- TeaVMWebSocketClient: socket events are queued in JavaScript and applied from the game's loop, not in
  the browser's callbacks (a message arriving while the game waited on IndexedDB crashed it on busy servers).
- StandaloneTeaVMLinker: classes or methods the browser doesn't have, used by mods, no longer stop the
  build. TeaVM compiles each such spot to throw NoClassDefFoundError/NoSuchMethodError if it runs; the
  build log lists them ("was not found"). Any other compiler error still stops the build.
"""
import os, sys


def patch(path, old, new, what):
    s = open(path).read()
    if new in s:
        return
    if old not in s:
        sys.exit("eagler-modloader: %s changed; check the %s patch" % (os.path.basename(path), what))
    open(path, "w").write(s.replace(old, new, 1))


def append_to_class(path, code, marker, what):
    """Adds code before the class's closing brace (once)."""
    s = open(path).read()
    if marker in s:
        return
    end = s.rstrip().rfind("}")
    if end < 0:
        sys.exit("eagler-modloader: %s changed; check the %s patch" % (os.path.basename(path), what))
    open(path, "w").write(s[:end] + code + s[end:])


def main():
    ws = sys.argv[1]
    inj = os.path.join(ws, "teavm-compat/src/main/java/net/lax1dude/eaglercraft/v1_8/teavmcompat/plugin/JdkMethodInjector.java")
    anchor = '\t\t{ "Base64Inject", "java.util.Base64", false },\n'
    patch(inj, anchor, anchor + '\t\t{ "ServiceLoaderInject", "java.util.ServiceLoader", false },\n'
          '\t\t{ "ReaderInject", "java.io.Reader", false },\n'
          '\t\t{ "BufferedReaderInject", "java.io.BufferedReader", false },\n'
          '\t\t{ "WriterInject", "java.io.Writer", false },\n'
          '\t\t{ "InputStreamInject", "java.io.InputStream", false },\n'
          '\t\t{ "OutputStreamInject", "java.io.OutputStream", false },\n', "ServiceLoader and java.io donors")

    anchor = '\t\t{ "ConcurrentMapInject", "java.util.concurrent.ConcurrentMap" },\n'
    patch(inj, anchor, anchor + '\t\t{ "ArraysInject", "java.util.Arrays" }, // eagler-modloader: copyOfRange past the end\n',
          "Arrays.copyOfRange fix")

    inject = os.path.join(ws, "teavm-compat/src/main/java/net/lax1dude/eaglercraft/v1_8/teavmcompat/inject")
    pi = os.path.join(inject, "PatternInject.java")
    patch(pi, "return input -> ((Pattern) (Object) this).matcher(input).find();",
          "return net.lax1dude.eaglercraft.v1_8.teavmcompat.EmlPatternPredicate.find(this); // eagler-modloader",
          "Pattern.asPredicate fix")
    patch(pi, "return input -> ((Pattern) (Object) this).matcher(input).matches();",
          "return net.lax1dude.eaglercraft.v1_8.teavmcompat.EmlPatternPredicate.matches(this); // eagler-modloader",
          "Pattern.asMatchPredicate fix")
    setters = "".join(
        "\n\tpublic static void set%s(Object array, int index, %s value) { // added by eagler-modloader\n"
        "\t\tjava.lang.reflect.Array.set(array, index, %s.valueOf(value));\n\t}\n" % (n, t, b)
        for n, t, b in [("Boolean", "boolean", "Boolean"), ("Byte", "byte", "Byte"), ("Char", "char", "Character"),
                        ("Short", "short", "Short"), ("Int", "int", "Integer"), ("Long", "long", "Long"),
                        ("Float", "float", "Float"), ("Double", "double", "Double")])
    append_to_class(os.path.join(inject, "ArrayInject.java"), setters, "public static void setInt(", "Array setters")
    patch(os.path.join(inject, "FieldInject.java"), """	public Type getGenericType() {
		return (Type) (Object) getType();
	}""", """	public Type getGenericType() {
		Object t = net.lax1dude.eaglercraft.v1_8.teavmcompat.EmlGenericTypes.fieldType(this); // eagler-modloader
		if (t instanceof Type) return (Type) t;
		return (Type) (Object) getType();
	}""", "Field.getGenericType hook")
    ci = os.path.join(inject, "ClassInject.java")
    patch(ci, """	public Type getGenericSuperclass() {
		return (Type) (Object) getSuperclass();
	}""", """	public Type getGenericSuperclass() {
		Object t = net.lax1dude.eaglercraft.v1_8.teavmcompat.EmlGenericTypes.classTypes(this, 0); // eagler-modloader
		if (t instanceof Type) return (Type) t;
		return (Type) (Object) getSuperclass();
	}""", "Class.getGenericSuperclass hook")
    patch(ci, """	public Type[] getGenericInterfaces() {
""", """	public Type[] getGenericInterfaces() {
		Object t = net.lax1dude.eaglercraft.v1_8.teavmcompat.EmlGenericTypes.classTypes(this, 1); // eagler-modloader
		if (t instanceof Type[]) return (Type[]) t;
""", "Class.getGenericInterfaces hook")
    patch(ci, """	public java.lang.reflect.TypeVariable[] getTypeParameters() {
		return new java.lang.reflect.TypeVariable[0];""", """	public java.lang.reflect.TypeVariable[] getTypeParameters() {
		Object t = net.lax1dude.eaglercraft.v1_8.teavmcompat.EmlGenericTypes.classTypes(this, 2); // eagler-modloader
		if (t instanceof java.lang.reflect.TypeVariable[]) return (java.lang.reflect.TypeVariable[]) t;
		return new java.lang.reflect.TypeVariable[0];""", "Class.getTypeParameters hook")
    append_to_class(os.path.join(inject, "FilesInject.java"),
        "\n\tpublic static java.nio.file.DirectoryStream<Path> newDirectoryStream(Path dir, String glob) throws IOException { // added by eagler-modloader\n"
        "\t\treturn net.lax1dude.eaglercraft.v1_8.teavmcompat.EmlNio.newDirectoryStream(dir, glob);\n\t}\n",
        "newDirectoryStream(Path dir, String glob)", "Files.newDirectoryStream")

    glfw = os.path.join(ws, "platform-teavm/src/main/java/org/lwjgl/glfw/GLFW.java")
    anchor = "\tpublic static String glfwGetKeyName("
    patch(glfw, anchor, "\tpublic static int glfwGetMouseButton(final long window, final int button) {\n"
          "\t\treturn PlatformInput.mouseIsButtonDown(button) ? 1 : 0; // added by eagler-modloader\n\t}\n\n" + anchor,
          "glfwGetMouseButton")

    sfl = os.path.join(ws, "game/src/main/java/net/minecraft/server/ServerFunctionLibrary.java")
    old = """               result.put(id, CompletableFuture.supplyAsync(() -> {
                  List<String> lines = readLines(entry.getValue());
                  return CommandFunction.fromLines(id, this.dispatcher, compilationContext, lines);
               }, taskExecutor));"""
    new = """               // eagler-modloader: compiled right away; the browser's server can't wait on a task
               CompletableFuture<CommandFunction<CommandSourceStack>> compiled = new CompletableFuture<>();
               try {
                  compiled.complete(CommandFunction.fromLines(id, this.dispatcher, compilationContext, readLines(entry.getValue())));
               } catch (Throwable t) {
                  compiled.completeExceptionally(t);
               }
               result.put(id, compiled);"""
    patch(sfl, old, new, "datapack functions")
    # and read each result directly: Eaglercraft's handle() completes its new future later, even on a
    # finished one, so handle(...).join() always stopped the world from loading
    old = """               functionFutures.forEach((id, functionFuture) -> functionFuture.handle((function, throwable) -> {
                  if (throwable != null) {
                     LOGGER.error("Failed to load function {}", id, throwable);
                  } else {
                     newFunctions.put(id, function);
                  }

                  return null;
               }).join());"""
    new = """               functionFutures.forEach((id, functionFuture) -> { // eagler-modloader: no join (see above)
                  if (!functionFuture.isDone()) {
                     LOGGER.error("Function {} was not ready", id);
                     return;
                  }
                  try {
                     newFunctions.put(id, functionFuture.join());
                  } catch (Throwable throwable) {
                     LOGGER.error("Failed to load function {}", id, throwable);
                  }
               });"""
    patch(sfl, old, new, "datapack function results")

    linker = os.path.join(ws, "wasm-toolchain/StandaloneTeaVMLinker.java")
    old = """        if (!result.getProblems().getSevereProblems().isEmpty()) {
            throw new IllegalStateException("TeaVM reported severe problems");
        }"""
    new = """        int missing = 0, other = 0;
        for (var p : result.getProblems().getSevereProblems()) {
            if (String.valueOf(p.getText()).contains("was not found")) missing++; else other++;
        }
        if (missing > 0) {
            System.out.println("[eagler-modloader] " + missing + " uses of classes or methods the browser doesn't have"
                + " (listed above as 'was not found'); that code throws an error if it runs");
        }
        if (other > 0) {
            throw new IllegalStateException("TeaVM reported severe problems");
        }"""
    patch(linker, old, new, "missing-class handling")
    prog = os.path.join(ws, "platform-teavm/src/main/java/net/lax1dude/eaglercraft/v1_8/internal/webgl2/WebGL2Program.java")
    old = """		gl.compileShader(shaderId);
		if (!gl.getShaderParameterb(shaderId, WebGL2Const.GL_COMPILE_STATUS)) {
			String logInfo = gl.getShaderInfoLog(shaderId);"""
    new = """		gl.compileShader(shaderId);
		for (int emlTry = 0; emlTry < 4 && !gl.getShaderParameterb(shaderId, WebGL2Const.GL_COMPILE_STATUS); emlTry++) {
			// eagler-modloader: GLSL ES has no implicit int-to-float conversion; fix the lines it complained about
			String emlFixed = emlFloatLiterals(esSource, gl.getShaderInfoLog(shaderId));
			if (emlFixed == null) break;
			esSource = emlFixed;
			gl.shaderSource(shaderId, esSource);
			gl.compileShader(shaderId);
			if (gl.getShaderParameterb(shaderId, WebGL2Const.GL_COMPILE_STATUS)) {
				System.err.println("eagler-webgl2: " + type.getName() + " shader (" + id + ") compiled after making whole numbers decimal");
			}
		}
		if (!gl.getShaderParameterb(shaderId, WebGL2Const.GL_COMPILE_STATUS)) {
			String logInfo = gl.getShaderInfoLog(shaderId);"""
    patch(prog, old, new, "shader whole-number retry")
    append_to_class(prog, """
	/**
	 * eagler-modloader: desktop GLSL converts int to float implicitly (1 - x), GLSL ES 3.00 doesn't. For each
	 * compiler error about an int, the whole-number literals on that line next to the operator it names
	 * (or all on the line, for a function call or assignment) become float literals; never inside [].
	 * Null if nothing changed.
	 */
	static String emlFloatLiterals(String src, String log) {
		if (log == null) return null;
		String[] lines = src.split("\\n", -1);
		// the line each error names, as an index into lines: #line directives (the game's preprocessor adds
		// them) set the number of the line after them
		java.util.Map<String, Integer> at = new java.util.HashMap<>();
		int num = 1, str = 0;
		for (int i = 0; i < lines.length; i++) {
			String t = lines[i].trim();
			if (t.startsWith("#line")) {
				String[] w = t.substring(5).trim().split("\\\\s+");
				try {
					num = Integer.parseInt(w[0]);
					if (w.length > 1) str = Integer.parseInt(w[1]);
					continue;
				} catch (RuntimeException e) {
					// not a number: count it as a line
				}
			}
			at.put(str + ":" + num, i);
			num++;
		}
		boolean changed = false;
		for (String err : log.split("\\n")) {
			if (err.indexOf("int") < 0) continue;
			int a = err.indexOf("ERROR:");
			if (a < 0) continue;
			int b = err.indexOf(':', a + 6);
			int c = b < 0 ? -1 : err.indexOf(':', b + 1);
			if (c < 0) continue;
			Integer found = at.get(err.substring(a + 6, b).trim() + ":" + err.substring(b + 1, c).trim());
			if (found == null) continue;
			int ln = found;
			b = c;
			if (ln < 0 || ln >= lines.length || lines[ln].trim().startsWith("#")) continue;
			String op = null;
			int q1 = err.indexOf('\\'', b), q2 = q1 < 0 ? -1 : err.indexOf('\\'', q1 + 1);
			if (q1 >= 0 && q2 > q1) op = err.substring(q1 + 1, q2);
			boolean whole = err.indexOf("no matching overloaded function") >= 0 || err.indexOf("cannot convert") >= 0;
			if (!whole && (op == null || op.isEmpty() || "+-*/<>=!".indexOf(op.charAt(0)) < 0)) continue;
			String fixed = emlFloatLine(lines[ln], whole ? null : op);
			if (!fixed.equals(lines[ln])) {
				lines[ln] = fixed;
				changed = true;
			}
		}
		return changed ? String.join("\\n", lines) : null;
	}

	private static boolean emlWordChar(char c) {
		return Character.isLetterOrDigit(c) || c == '_' || c == '.';
	}

	private static String emlFloatLine(String line, String op) {
		StringBuilder out = new StringBuilder(line.length() + 8);
		int depth = 0, n = line.length();
		for (int i = 0; i < n; i++) {
			char c = line.charAt(i);
			if (c == '[') depth++;
			else if (c == ']') depth--;
			if (Character.isDigit(c) && (i == 0 || !emlWordChar(line.charAt(i - 1)))) {
				int j = i;
				while (j < n && Character.isDigit(line.charAt(j))) j++;
				out.append(line, i, j);
				if ((j >= n || !emlWordChar(line.charAt(j))) && depth == 0 && (op == null || emlNextTo(line, i, j, op))) out.append(".0");
				i = j - 1;
				continue;
			}
			out.append(c);
		}
		return out.toString();
	}

	private static boolean emlNextTo(String line, int i, int j, String op) {
		int k = i - 1;
		while (k >= 0 && line.charAt(k) == ' ') k--;
		if (k - op.length() + 1 >= 0 && line.startsWith(op, k - op.length() + 1)) return true;
		k = j;
		while (k < line.length() && line.charAt(k) == ' ') k++;
		return line.startsWith(op, k);
	}
""", "static String emlFloatLiterals(", "shader whole-number helper")
    # WebSocket events are queued in JavaScript and handed to the game from its own loop (emlPoll), instead
    # of running game code inside the browser's event callbacks: a server message arriving while the game
    # was paused waiting on the browser (saving options to IndexedDB) re-entered it and crashed it
    # (illegal cast in TeaVMWebSocketClient$3, Fiber stack overflow), on busy servers.
    wsc = os.path.join(ws, "platform-teavm/src/main/java/net/lax1dude/eaglercraft/v1_8/internal/teavm/TeaVMWebSocketClient.java")
    patch(wsc, "import org.teavm.jso.JSBody;\n", "import org.teavm.jso.JSBody;\nimport org.teavm.jso.JSObject;\nimport org.teavm.jso.core.JSArrayReader;\nimport java.util.List;\nimport net.lax1dude.eaglercraft.v1_8.internal.IWebSocketFrame;\n", "websocket imports")
    s = open(wsc).read()
    a = s.find("\t\topenListener = new EventListener<Event>() {")
    b = s.find("\t\tsock.addEventListener(\"error\", errorListener);\n")
    if "emlQueue = emlListen(sock);" not in s:
        if a < 0 or b < 0:
            sys.exit("eagler-modloader: TeaVMWebSocketClient.java changed; check the websocket queue patch")
        b += len("\t\tsock.addEventListener(\"error\", errorListener);\n")
        s = s[:a] + "\t\topenListener = null;\n\t\tcloseListener = null;\n\t\tmessageListener = null;\n\t\terrorListener = null;\n\t\temlQueue = emlListen(sock);\n" + s[b:]
        open(wsc, "w").write(s)
    patch(wsc, "\tprivate boolean closeStarted;\n", "\tprivate boolean closeStarted;\n\tprivate JSObject emlQueue;\n", "websocket queue field")
    patch(wsc, "\t\tclearFrames();\n\t\tcloseSocketAfterCallback(sock, openListener, closeListener, messageListener, errorListener);",
        "\t\tclearFrames();\n\t\temlCloseLater(sock, emlQueue);", "websocket queue close")
    patch(wsc, "\t\twhile(sockIsConnecting && !sockIsConnected && !sockIsFailed) {\n\t\t\tEagUtils.sleep(50);",
        "\t\temlPoll();\n\t\twhile(sockIsConnecting && !sockIsConnected && !sockIsFailed) {\n\t\t\tEagUtils.sleep(50);\n\t\t\temlPoll();", "websocket queue connect")
    patch(wsc, "\t\t\twhile(closeCode == 0 && PlatformRuntime.steadyTimeMillis() < closeDeadline) {\n\t\t\t\tEagUtils.sleep(10);",
        "\t\t\twhile(closeCode == 0 && PlatformRuntime.steadyTimeMillis() < closeDeadline) {\n\t\t\t\tEagUtils.sleep(10);\n\t\t\t\temlPoll();", "websocket queue connect close")
    for sig in ["public EnumEaglerConnectionState getState() {\n", "public boolean isOpen() {\n", "public boolean isClosed() {\n",
                "public int getCloseCode() {\n", "public String getCloseReason() {\n", "public void send(String str) {\n", "public void send(byte[] bytes) {\n"]:
        patch(wsc, "\t" + sig, "\t" + sig + "\t\temlPoll();\n", "websocket queue " + sig.split("(")[0])
    append_to_class(wsc, r'''
	// eagler-modloader: the socket's events, queued in JavaScript (see emlPoll)
	@JSBody(params = { "sock" }, script =
			"var q = { ev: [], fns: null };"
			+ "var fo = function() { q.ev.push({ t: 0 }); };"
			+ "var fc = function(e) { q.ev.push({ t: 1, c: (e && e.code) | 0, r: e && e.reason != null ? String(e.reason) : null }); };"
			+ "var fm = function(e) { q.ev.push({ t: 2, d: e.data }); };"
			+ "var fe = function() { q.ev.push({ t: 3 }); };"
			+ "q.fns = [fo, fc, fm, fe];"
			+ "sock.addEventListener('open', fo); sock.addEventListener('close', fc);"
			+ "sock.addEventListener('message', fm); sock.addEventListener('error', fe);"
			+ "return q;")
	private static native JSObject emlListen(WebSocket sock);

	@JSBody(params = { "q" }, script = "var a = q.ev; q.ev = []; return a;")
	private static native JSArrayReader<JSObject> emlTake(JSObject q);

	@JSBody(params = { "e" }, script = "return e.t;")
	private static native int emlType(JSObject e);

	@JSBody(params = { "e" }, script = "return e.d;")
	private static native JSObject emlData(JSObject e);

	@JSBody(params = { "e" }, script = "return e.c;")
	private static native int emlCode(JSObject e);

	@JSBody(params = { "e" }, script = "return e.r;")
	private static native String emlReason(JSObject e);

	@JSBody(params = { "socket", "q" }, script =
			"var types = ['open', 'close', 'message', 'error'];"
			+ "if (q && q.fns) for (var i = 0; i < 4; i++) { try { socket.removeEventListener(types[i], q.fns[i]); } catch (e) {} }"
			+ "if (q) q.ev = [];"
			+ "var closeLater = function() { var state;"
			+ " try { state = socket.readyState; } catch (e) { return; }"
			+ " if (state === 2 || state === 3) return;"
			+ " try { socket.close(); } catch (e) {} };"
			+ "try { if (typeof queueMicrotask === 'function') queueMicrotask(closeLater);"
			+ " else setTimeout(closeLater, 0); } catch (e) {"
			+ " try { setTimeout(closeLater, 0); } catch (ignored) {} }")
	private static native void emlCloseLater(WebSocket socket, JSObject q);

	/** Applies the socket's queued events, in order, on the game's own thread. */
	private void emlPoll() {
		if (closeStarted || emlQueue == null) return;
		JSArrayReader<JSObject> evs = emlTake(emlQueue);
		int n = evs.getLength();
		for (int i = 0; i < n; i++) {
			JSObject e = evs.get(i);
			switch (emlType(e)) {
			case 0:
				sockIsConnecting = false;
				sockIsConnected = true;
				break;
			case 1:
				sockIsConnecting = false;
				sockIsConnected = false;
				closeCode = emlCode(e);
				closeReason = emlReason(e);
				break;
			case 2:
				addRecievedFrame(new TeaVMWebSocketFrame(emlData(e)));
				break;
			default:
				if (sockIsConnecting) {
					sockIsFailed = true;
					sockIsConnecting = false;
				}
				break;
			}
		}
	}

	@Override public int availableFrames() { emlPoll(); return super.availableFrames(); }
	@Override public IWebSocketFrame getNextFrame() { emlPoll(); return super.getNextFrame(); }
	@Override public List<IWebSocketFrame> getNextFrames() { emlPoll(); return super.getNextFrames(); }
	@Override public int availableStringFrames() { emlPoll(); return super.availableStringFrames(); }
	@Override public IWebSocketFrame getNextStringFrame() { emlPoll(); return super.getNextStringFrame(); }
	@Override public List<IWebSocketFrame> getNextStringFrames() { emlPoll(); return super.getNextStringFrames(); }
	@Override public int availableBinaryFrames() { emlPoll(); return super.availableBinaryFrames(); }
	@Override public IWebSocketFrame getNextBinaryFrame() { emlPoll(); return super.getNextBinaryFrame(); }
	@Override public List<IWebSocketFrame> getNextBinaryFrames() { emlPoll(); return super.getNextBinaryFrames(); }
''', "private void emlPoll()", "websocket queue helpers")
    print("workspace: ServiceLoader.findFirst, Reader.transferTo, Array setters, Files.newDirectoryStream, glfwGetMouseButton,"
          " datapack functions, shader whole numbers; mods' missing classes don't stop the build")


if __name__ == "__main__":
    main()
