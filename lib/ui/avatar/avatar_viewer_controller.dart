/// Renderer-neutral controls still required while Ava migrates from embedded
/// whole-body clips to the production semantic motion graph.
abstract interface class AvaAvatarViewerController {
  Future<List<String>> getAvailableAnimations();

  void setCameraTarget(double x, double y, double z);

  void setFrontCameraOrbit(double theta, double phi, double radius);

  void recenterCamera();

  int playAnimation({
    required String animationName,
    required int loopCount,
    double playbackRate = 1,
  });

  void dispose();
}
