cmake_minimum_required(VERSION 3.10)
project(messy CXX)

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2 -std=c++17")

include_directories(include)
add_definitions(-DMESSY_BUILD=1)

option(BUILD_TESTS "build the tests" ON)

file(GLOB SOURCES src/*.cpp)
add_library(messy ${SOURCES})

include(FetchContent)
FetchContent_Declare(fmt
  GIT_REPOSITORY https://github.com/fmtlib/fmt.git
  GIT_TAG master)
FetchContent_Declare(json
  GIT_REPOSITORY https://github.com/nlohmann/json.git)
FetchContent_Declare(spdlog
  GIT_REPOSITORY https://github.com/gabime/spdlog.git
  GIT_TAG v1.14.1)
FetchContent_Declare(catch
  GIT_REPOSITORY https://github.com/catchorg/Catch2.git
  GIT_TAG devel)
FetchContent_MakeAvailable(fmt json spdlog catch)

add_executable(messy_app src/main.cpp)
target_link_libraries(messy_app messy)

install(TARGETS messy DESTINATION lib)
