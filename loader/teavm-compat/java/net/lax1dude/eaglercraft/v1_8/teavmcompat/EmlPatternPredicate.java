package net.lax1dude.eaglercraft.v1_8.teavmcompat;

import java.util.function.Predicate;
import java.util.regex.Pattern;

/**
 * Added by eagler-modloader: what Pattern.asPredicate() and asMatchPredicate() return. Eaglercraft's
 * donor returned a lambda, and lambdas inside donors don't survive being copied into the JDK class, so
 * both methods returned null (AppleSkin's settings library, Jankson, crashed on it at startup).
 */
public final class EmlPatternPredicate implements Predicate<String> {
	private final Pattern pattern;
	private final boolean whole;

	private EmlPatternPredicate(Pattern pattern, boolean whole) {
		this.pattern = pattern;
		this.whole = whole;
	}

	public static Predicate<String> find(Object pattern) {
		return new EmlPatternPredicate((Pattern) pattern, false);
	}

	public static Predicate<String> matches(Object pattern) {
		return new EmlPatternPredicate((Pattern) pattern, true);
	}

	@Override
	public boolean test(String input) {
		return whole ? pattern.matcher(input).matches() : pattern.matcher(input).find();
	}
}
