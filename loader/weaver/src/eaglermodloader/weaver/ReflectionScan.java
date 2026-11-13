package eaglermodloader.weaver;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.FieldNode;
import org.objectweb.asm.tree.InnerClassNode;
import org.objectweb.asm.tree.LdcInsnNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;
import org.objectweb.asm.tree.TypeInsnNode;

/**
 * Lets mods read and write fields by reflection (config libraries such as Mod Menu's save settings
 * this way). The browser compiler only keeps field readers/writers for fields it's told about, and has
 * no generic types.
 *
 * Mod code that uses reflection (Class.getDeclaredFields(), getDeclaredConstructor(), newInstance(), ...)
 * or hands objects to Gson (which builds and fills them by reflection) marks the classes named in the
 * same method (X.class, casts, fields it reads) and its own class as reflected; so does any mod class
 * named as a class literal anywhere in mod code (usually handed to such a library). With them come the types
 * of their fields and their superclasses, so nested settings objects work too. Fields, constructors
 * and methods of reflected classes stay available to reflection. They're
 * written to eml-reflect.txt in the game classes ("C class"), which the reflection supplier added to
 * Eaglercraft's TeaVM plugin reads, so all their fields stay readable and writable. Their fields with
 * generic types are listed too ("G class#field signature") for GeneratedMods.genericFieldType(), and
 * mods' Field.getGenericType() calls are pointed at EmlReflect.getGenericType(), which uses that table.
 * Mods' Class/ClassLoader.getResourceAsStream() calls are pointed at EmlResources, which also finds the
 * mods' own files.
 */
final class ReflectionScan {
	private ReflectionScan() {
	}

	private static final Set<String> REFLECT = Set.of("getDeclaredFields", "getFields", "getDeclaredField", "getField",
			"getDeclaredMethods", "getMethods", "getDeclaredMethod", "getMethod", "getDeclaredConstructors", "getConstructors",
			"getDeclaredConstructor", "getConstructor", "newInstance", "getRecordComponents");
	private static final String[] JDK_COLLECTIONS = { "java.util.ArrayList", "java.util.LinkedList", "java.util.HashMap",
			"java.util.LinkedHashMap", "java.util.TreeMap", "java.util.HashSet", "java.util.LinkedHashSet", "java.util.TreeSet",
			"java.util.ArrayDeque", "java.util.Vector", "java.util.concurrent.ConcurrentHashMap", "java.util.concurrent.CopyOnWriteArrayList" };
	private static final String[] GAME = { "net/minecraft/", "com/mojang/", "net/lax1dude/", "org/spongepowered/", "net/fabricmc/loader/", "eaglermodloader/" };

	static void apply(Path classes) throws IOException {
		List<Path> files = new ArrayList<>();
		try (Stream<Path> s = Files.walk(classes)) {
			s.filter(p -> p.toString().endsWith(".class")).forEach(files::add);
		}
		Set<String> reflected = new TreeSet<>();
		int rewired = 0, resources = 0;
		for (Path p : files) {
			String name = classes.relativize(p).toString().replace('\\', '/');
			name = name.substring(0, name.length() - 6);
			if (isGame(name)) continue;
			byte[] b = Files.readAllBytes(p);
			ClassNode cn = new ClassNode();
			new ClassReader(b).accept(cn, 0);
			boolean changed = false;
			// libraries bundled inside mods (ReplayMod ships a web server, Guava, image readers, ...) name
			// classes everywhere for their own loggers and internals; they don't decide what mods reflect on,
			// and counting them made the program too big for the compiler's memory
			boolean library = isLibrary(name);
			for (MethodNode m : cn.methods) {
				boolean lists = false;
				Set<String> named = new TreeSet<>();
				for (AbstractInsnNode in = m.instructions.getFirst(); in != null; in = in.getNext()) {
					if (in instanceof MethodInsnNode mi) {
						if ((mi.owner.equals("java/lang/Class") || mi.owner.equals("java/lang/reflect/Constructor")) && REFLECT.contains(mi.name)) lists = true;
						if (mi.owner.startsWith("com/google/gson/")) lists = true; // Gson builds and fills objects by reflection
						if (mi.owner.equals("java/lang/reflect/Field") && mi.name.equals("getGenericType") && mi.desc.equals("()Ljava/lang/reflect/Type;")) {
							mi.setOpcode(Opcodes.INVOKESTATIC);
							mi.owner = "eaglermodloader/runtime/EmlReflect";
							mi.desc = "(Ljava/lang/reflect/Field;)Ljava/lang/reflect/Type;";
							mi.itf = false;
							changed = true;
							rewired++;
						}
						// mods' own files: no class path in the browser (see EmlResources)
						if (mi.name.equals("getResourceAsStream") && mi.desc.equals("(Ljava/lang/String;)Ljava/io/InputStream;")
								&& (mi.owner.equals("java/lang/Class") || mi.owner.equals("java/lang/ClassLoader"))) {
							mi.desc = "(L" + mi.owner + ";Ljava/lang/String;)Ljava/io/InputStream;";
							mi.setOpcode(Opcodes.INVOKESTATIC);
							mi.owner = "eaglermodloader/runtime/EmlResources";
							mi.itf = false;
							changed = true;
							resources++;
						}
					} else if (in instanceof LdcInsnNode ldc && ldc.cst instanceof Type t && t.getSort() == Type.OBJECT) {
						named.add(t.getInternalName());
					} else if (in instanceof TypeInsnNode ti && ti.getOpcode() == Opcodes.CHECKCAST) {
						named.add(Type.getObjectType(ti.desc).getSort() == Type.OBJECT ? ti.desc : Type.getType(ti.desc).getElementType().getInternalName());
					} else if (in instanceof FieldInsnNode fi) {
						Type ft = Type.getType(fi.desc);
						if (ft.getSort() == Type.ARRAY) ft = ft.getElementType();
						if (ft.getSort() == Type.OBJECT) named.add(ft.getInternalName());
					}
				}
				if (lists && !library) {
					reflected.add(cn.name);
					for (String n : named) if (!n.startsWith("java/") && !isGame(n)) reflected.add(n);
				}
				// a mod class named as a class literal (Config.class) is nearly always handed to a library
				// that uses reflection on it (Cloth Config's AutoConfig.register, YACL, Gson, ...)
				for (AbstractInsnNode in = library ? null : m.instructions.getFirst(); in != null; in = in.getNext()) {
					if (in instanceof LdcInsnNode ldc && ldc.cst instanceof Type t && t.getSort() == Type.OBJECT) {
						String n = t.getInternalName();
						if (!n.startsWith("java/") && !isGame(n)) reflected.add(n);
					}
				}
			}
			if (changed) {
				ClassWriter cw = new ClassWriter(0);
				cn.accept(cw);
				Files.write(p, cw.toByteArray());
			}
		}
		Map<String, ClassNode> cache = new HashMap<>();
		// the types of reflected classes' fields (nested settings objects) and their superclasses
		List<String> todo = new ArrayList<>(reflected);
		while (!todo.isEmpty()) {
			ClassNode cn = read(classes, todo.remove(todo.size() - 1), cache);
			if (cn == null) continue;
			Set<String> more = new TreeSet<>();
			if (cn.superName != null) more.add(cn.superName);
			for (FieldNode f : cn.fields) {
				if ((f.access & Opcodes.ACC_STATIC) != 0 && (f.access & Opcodes.ACC_FINAL) != 0) continue;
				Type ft = Type.getType(f.desc);
				if (ft.getSort() == Type.ARRAY) ft = ft.getElementType();
				if (ft.getSort() == Type.OBJECT) more.add(ft.getInternalName());
				if (f.signature != null) {
					for (String part : f.signature.split("[<;>]")) {
						int l = part.indexOf('L');
						if (l >= 0 && part.length() > l + 1) more.add(part.substring(l + 1));
					}
				}
			}
			for (String n : more) {
				if (n.startsWith("java/") || isGame(n) || reflected.contains(n) || read(classes, n, cache) == null) continue;
				reflected.add(n);
				if (!isLibrary(n)) todo.add(n); // a library's own fields aren't settings
			}
		}
		StringBuilder out = new StringBuilder();
		int generic = 0;
		Set<String> byName = new TreeSet<>(); // non-public classes the generic types name: loaded with Class.forName
		for (String c : reflected) {
			ClassNode cn = read(classes, c, cache);
			if (cn == null) continue;
			out.append("C ").append(c.replace('/', '.')).append('\n');
			for (FieldNode f : cn.fields) {
				if (f.signature == null || !f.signature.startsWith("L") || !nameable(classes, f.signature, cache, reflected, byName)) continue;
				out.append("G ").append(c.replace('/', '.')).append('#').append(f.name).append(' ').append(f.signature).append('\n');
				generic++;
			}
		}
		// Gson creates fields declared as a concrete collection (LinkedHashMap, ArrayList, ...) through
		// their no-argument constructor by reflection, so those constructors must exist too
		for (String c : JDK_COLLECTIONS) out.append("C ").append(c).append('\n');
		for (String c : byName) out.append("N ").append(c.replace('/', '.')).append('\n');
		Files.write(classes.resolve("eml-reflect.txt"), out.toString().getBytes(StandardCharsets.UTF_8));
		System.out.println("[compat] reflection: " + reflected.size() + " classes readable by reflection, " + generic
				+ " generic field types, " + rewired + " getGenericType calls rewired, " + resources + " getResourceAsStream calls pointed at the mods' files");
	}

	private static final String[] LIBRARY_ROOTS = { "com/google/", "org/apache/", "org/eclipse/", "io/netty/", "it/unimi/",
			"com/twelvemonkeys/", "org/slf4j/", "org/objectweb/", "com/llamalad7/", "org/yaml/", "com/github/benmanes/",
			"javax/", "org/jetbrains/", "org/intellij/", "kotlin/", "org/joml/", "org/lwjgl/", "com/electronwill/" };
	private static final String[] LIBRARY_PARTS = { "/lib/", "/libs/", "/shadow/", "/shadowed/", "/shaded/", "/repack/",
			"/repackaged/", "/relocated/", "/thirdparty/", "/third_party/", "/external/" };

	static boolean isLibrary(String name) {
		for (String r : LIBRARY_ROOTS) if (name.startsWith(r)) return true;
		for (String r : LIBRARY_PARTS) if (name.contains(r)) return true;
		return false;
	}

	private static boolean isGame(String name) {
		for (String g : GAME) if (name.startsWith(g)) return true;
		return false;
	}

	private static ClassNode read(Path classes, String name, Map<String, ClassNode> cache) throws IOException {
		if (cache.containsKey(name)) return cache.get(name);
		Path p = classes.resolve(name + ".class");
		ClassNode cn = null;
		if (Files.isRegularFile(p)) {
			cn = new ClassNode();
			new ClassReader(Files.readAllBytes(p)).accept(cn, ClassReader.SKIP_CODE);
		}
		cache.put(name, cn);
		return cn;
	}

	/**
	 * True if generated code can name every class in the signature: public, from the JDK, or reflected
	 * (then it's loaded by name, and added to byName).
	 */
	private static boolean nameable(Path classes, String sig, Map<String, ClassNode> cache, Set<String> reflected, Set<String> byName)
			throws IOException {
		Set<String> need = new TreeSet<>();
		for (int i = 0; i < sig.length(); i++) {
			boolean typeStart = i == 0 || "<;+-[".indexOf(sig.charAt(i - 1)) >= 0;
			if (!typeStart) continue;
			char c = sig.charAt(i);
			if (c == 'T') return false; // a type variable: not known here
			if (c != 'L') continue;
			int end = i + 1;
			while (end < sig.length() && "<;".indexOf(sig.charAt(end)) < 0) end++;
			String n = sig.substring(i + 1, end);
			if (n.startsWith("java/")) continue;
			ClassNode cn = read(classes, n, cache);
			if (cn == null) return false;
			boolean pub = (cn.access & Opcodes.ACC_PUBLIC) != 0 && (!n.contains("$") || nestedPublic(cn, n));
			if (pub) continue;
			if (!reflected.contains(n)) return false;
			need.add(n);
		}
		byName.addAll(need);
		return true;
	}

	private static boolean nestedPublic(ClassNode cn, String n) {
		for (InnerClassNode ic : cn.innerClasses) {
			if (ic.name.equals(n)) return (ic.access & Opcodes.ACC_PUBLIC) != 0 && ic.innerName != null;
		}
		return false;
	}
}
