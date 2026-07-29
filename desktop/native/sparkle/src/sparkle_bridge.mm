#import <Cocoa/Cocoa.h>
#import <Sparkle/Sparkle.h>

#include <node_api.h>

static SPUStandardUpdaterController *sparkleController = nil;

static napi_value ThrowError(napi_env env, NSString *message) {
  napi_throw_error(env, nullptr, message.UTF8String);
  return nullptr;
}

static bool EnsureMainThread(napi_env env) {
  if (NSThread.isMainThread) {
    return true;
  }

  ThrowError(env, @"Sparkle must be called from Electron's main thread.");
  return false;
}

static void StartController() {
  if (sparkleController == nil) {
    sparkleController = [[SPUStandardUpdaterController alloc]
        initWithStartingUpdater:YES
                updaterDelegate:nil
             userDriverDelegate:nil];
  }
}

static napi_value Start(napi_env env, napi_callback_info info) {
  (void)info;
  if (!EnsureMainThread(env)) {
    return nullptr;
  }

  @try {
    StartController();
  } @catch (NSException *exception) {
    return ThrowError(env, exception.reason ?: exception.name);
  }

  napi_value result;
  napi_get_undefined(env, &result);
  return result;
}

static napi_value CheckForUpdates(napi_env env, napi_callback_info info) {
  (void)info;
  if (!EnsureMainThread(env)) {
    return nullptr;
  }

  @try {
    StartController();
    [sparkleController checkForUpdates:nil];
  } @catch (NSException *exception) {
    return ThrowError(env, exception.reason ?: exception.name);
  }

  napi_value result;
  napi_get_undefined(env, &result);
  return result;
}

static napi_value Initialize(napi_env env, napi_value exports) {
  napi_value startFunction;
  napi_create_function(env, "start", NAPI_AUTO_LENGTH, Start, nullptr, &startFunction);
  napi_set_named_property(env, exports, "start", startFunction);

  napi_value checkFunction;
  napi_create_function(
      env,
      "checkForUpdates",
      NAPI_AUTO_LENGTH,
      CheckForUpdates,
      nullptr,
      &checkFunction);
  napi_set_named_property(env, exports, "checkForUpdates", checkFunction);

  return exports;
}

NAPI_MODULE(sparkle_bridge, Initialize)
