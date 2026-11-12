import 'package:flutter/material.dart';

/// Brand colours extracted from the FileSync logo.
abstract final class AppColors {
  // Navy backgrounds
  static const navy900 = Color(0xFF0F1923);
  static const navy800 = Color(0xFF172535);
  static const navy700 = Color(0xFF1E2D3D);
  static const navy600 = Color(0xFF253547);
  static const navy400 = Color(0xFF2A4A6B);

  // Cyan accent (sync arrows)
  static const cyan400 = Color(0xFF38BDF8);
  static const cyan300 = Color(0xFF60A5FA);
  static const cyan200 = Color(0xFF93C5FD);

  // Surface / text
  static const white = Color(0xFFF0F6FF);
  static const muted = Color(0xFF8BA5C0);
}

abstract final class AppTheme {
  static const _lightSeed = AppColors.cyan400;
  static const _darkSeed = AppColors.cyan400;

  // ── Light theme ────────────────────────────────────────────────────────────

  static ThemeData get light {
    final cs = ColorScheme.fromSeed(
      seedColor: _lightSeed,
      brightness: Brightness.light,
      primary: AppColors.cyan400,
      onPrimary: AppColors.navy900,
      secondary: AppColors.cyan300,
      onSecondary: AppColors.navy900,
      surface: const Color(0xFFF4F8FC),
      onSurface: AppColors.navy800,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: cs,
      scaffoldBackgroundColor: const Color(0xFFEFF4FA),

      appBarTheme: AppBarTheme(
        backgroundColor: cs.surface,
        foregroundColor: AppColors.navy800,
        elevation: 0,
        scrolledUnderElevation: 1,
        titleTextStyle: const TextStyle(
          color: AppColors.navy800,
          fontSize: 20,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.3,
        ),
      ),

      cardTheme: CardThemeData(
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          // FIXED: withOpacity -> withValues
          side: BorderSide(color: AppColors.cyan400.withValues(alpha: 0.15)),
        ),
        color: Colors.white,
        margin: const EdgeInsets.symmetric(vertical: 5, horizontal: 0),
      ),

      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: Colors.white,
        // FIXED: withOpacity -> withValues
        indicatorColor: AppColors.cyan400.withValues(alpha: 0.18),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const IconThemeData(color: AppColors.cyan400);
          }
          // FIXED: withOpacity -> withValues
          return IconThemeData(color: AppColors.navy800.withValues(alpha: 0.5));
        }),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const TextStyle(
              color: AppColors.cyan400,
              fontWeight: FontWeight.w600,
              fontSize: 12,
            );
          }
          // FIXED: withOpacity -> withValues
          return TextStyle(
            color: AppColors.navy800.withValues(alpha: 0.5),
            fontSize: 12,
          );
        }),
      ),

      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: Colors.white,
        selectedIconTheme: const IconThemeData(color: AppColors.cyan400),
        unselectedIconTheme: IconThemeData(
          // FIXED: withOpacity -> withValues
          color: AppColors.navy800.withValues(alpha: 0.4),
        ),
        // FIXED: withOpacity -> withValues
        indicatorColor: AppColors.cyan400.withValues(alpha: 0.18),
        selectedLabelTextStyle: const TextStyle(
          color: AppColors.cyan400,
          fontWeight: FontWeight.w600,
          fontSize: 12,
        ),
        unselectedLabelTextStyle: TextStyle(
          // FIXED: withOpacity -> withValues
          color: AppColors.navy800.withValues(alpha: 0.45),
          fontSize: 12,
        ),
      ),

      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.cyan400,
          foregroundColor: AppColors.navy900,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
          ),
          textStyle: const TextStyle(fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.navy800,
          // FIXED: withOpacity -> withValues
          side: BorderSide(color: AppColors.navy800.withValues(alpha: 0.25)),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
          ),
          textStyle: const TextStyle(fontWeight: FontWeight.w500),
        ),
      ),

      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.cyan400,
      ),

      dividerTheme: DividerThemeData(
        // FIXED: withOpacity -> withValues
        color: AppColors.navy400.withValues(alpha: 0.12),
        thickness: 1,
        space: 1,
      ),

      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return AppColors.cyan400;
          return Colors.transparent;
        }),
        checkColor: WidgetStateProperty.all(AppColors.navy900),
        // FIXED: withOpacity -> withValues
        side: BorderSide(
          color: AppColors.navy400.withValues(alpha: 0.4),
          width: 1.5,
        ),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
      ),

      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: const Color(0xFFF4F8FC),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          // FIXED: withOpacity -> withValues
          borderSide: BorderSide(
            color: AppColors.navy400.withValues(alpha: 0.2),
          ),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: AppColors.cyan400, width: 1.5),
        ),
        // FIXED: withOpacity -> withValues
        labelStyle: TextStyle(color: AppColors.navy800.withValues(alpha: 0.6)),
      ),

      iconTheme: IconThemeData(color: AppColors.navy800.withValues(alpha: 0.7)),
    );
  }

  // ── Dark theme ─────────────────────────────────────────────────────────────

  static ThemeData get dark {
    final cs = ColorScheme.fromSeed(
      seedColor: _darkSeed,
      brightness: Brightness.dark,
      primary: AppColors.cyan400,
      onPrimary: AppColors.navy900,
      secondary: AppColors.cyan300,
      onSecondary: AppColors.navy900,
      surface: AppColors.navy700,
      onSurface: AppColors.white,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: cs,
      scaffoldBackgroundColor: AppColors.navy900,

      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.navy800,
        foregroundColor: AppColors.white,
        elevation: 0,
        scrolledUnderElevation: 1,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: TextStyle(
          color: AppColors.white,
          fontSize: 20,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.3,
        ),
      ),

      cardTheme: CardThemeData(
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          // FIXED: withOpacity -> withValues
          side: BorderSide(color: AppColors.cyan400.withValues(alpha: 0.12)),
        ),
        color: AppColors.navy700,
        margin: const EdgeInsets.symmetric(vertical: 5, horizontal: 0),
      ),

      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: AppColors.navy800,
        // FIXED: withOpacity -> withValues
        indicatorColor: AppColors.cyan400.withValues(alpha: 0.22),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const IconThemeData(color: AppColors.cyan400);
          }
          return const IconThemeData(color: AppColors.muted);
        }),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const TextStyle(
              color: AppColors.cyan400,
              fontWeight: FontWeight.w600,
              fontSize: 12,
            );
          }
          return const TextStyle(color: AppColors.muted, fontSize: 12);
        }),
      ),

      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: AppColors.navy800,
        selectedIconTheme: const IconThemeData(color: AppColors.cyan400),
        unselectedIconTheme: const IconThemeData(color: AppColors.muted),
        // FIXED: withOpacity -> withValues
        indicatorColor: AppColors.cyan400.withValues(alpha: 0.2),
        selectedLabelTextStyle: const TextStyle(
          color: AppColors.cyan400,
          fontWeight: FontWeight.w600,
          fontSize: 12,
        ),
        unselectedLabelTextStyle: const TextStyle(
          color: AppColors.muted,
          fontSize: 12,
        ),
      ),

      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.cyan400,
          foregroundColor: AppColors.navy900,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
          ),
          textStyle: const TextStyle(fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.white,
          // FIXED: withOpacity -> withValues
          side: BorderSide(color: AppColors.cyan400.withValues(alpha: 0.3)),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
          ),
          textStyle: const TextStyle(fontWeight: FontWeight.w500),
        ),
      ),

      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.cyan400,
      ),

      dividerTheme: DividerThemeData(
        // FIXED: withOpacity -> withValues
        color: AppColors.cyan400.withValues(alpha: 0.1),
        thickness: 1,
        space: 1,
      ),

      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return AppColors.cyan400;
          return Colors.transparent;
        }),
        checkColor: WidgetStateProperty.all(AppColors.navy900),
        side: const BorderSide(color: AppColors.muted, width: 1.5),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
      ),

      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppColors.navy800,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          // FIXED: withOpacity -> withValues
          borderSide: BorderSide(
            color: AppColors.cyan400.withValues(alpha: 0.2),
          ),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: AppColors.cyan400, width: 1.5),
        ),
        labelStyle: const TextStyle(color: AppColors.muted),
      ),

      iconTheme: const IconThemeData(color: AppColors.muted),
    );
  }
}
