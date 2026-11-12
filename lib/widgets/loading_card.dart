import 'package:flutter/material.dart';

class LoadingCard extends StatelessWidget {
  final String text;

  const LoadingCard({super.key, required this.text});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Card(
        child: ListTile(
          contentPadding: const EdgeInsets.all(10),
          leading: const CircularProgressIndicator(),
          title: Text(text),
        ),
      ),
    );
  }
}
