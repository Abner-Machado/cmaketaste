cmake_minimum_required(VERSION 3.25)
project(tidylib VERSION 1.2.0 LANGUAGES CXX)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(TIDYLIB_BUILD_TESTS "Build the tidylib tests" OFF)

add_library(tidylib_warnings INTERFACE)
target_compile_options(tidylib_warnings INTERFACE
  $<$<CXX_COMPILER_ID:MSVC>:/W4>
  $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-Wall -Wextra -Wpedantic>)

add_library(tidylib src/tidy.cpp)
add_library(tidylib::tidylib ALIAS tidylib)
target_compile_features(tidylib PUBLIC cxx_std_20)
target_include_directories(tidylib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(tidylib PRIVATE tidylib_warnings)

include(FetchContent)
FetchContent_Declare(fmt
  GIT_REPOSITORY https://github.com/fmtlib/fmt.git
  GIT_TAG 10.2.1)
FetchContent_MakeAvailable(fmt)
find_package(Threads REQUIRED)
target_link_libraries(tidylib PUBLIC fmt::fmt Threads::Threads)

if(TIDYLIB_BUILD_TESTS)
  include(CTest)
  enable_testing()
  add_subdirectory(tests)
endif()

include(GNUInstallDirs)
install(TARGETS tidylib tidylib_warnings EXPORT tidylibTargets)
install(EXPORT tidylibTargets NAMESPACE tidylib:: DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/tidylib)
