pipeline {
  agent {
    node {
      label 'docker'
    }
  }
  stages {
   stage('Test Python 3.4') {
      agent {
        dockerfile {
          filename 'src/tests/python-34.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh 3.4.0'
          dir("tempsrc") { deleteDir() }
        }
        junit '3.4.0-results.xml'
      }
    }
    stage('Test Python 3.5.0') {
      agent {
        dockerfile {
          filename 'src/tests/python-350.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh 3.5.0'
          dir("tempsrc") { deleteDir() }
        }
        junit '3.5.0-results.xml'
      }
    }
    stage('Test Python 3.6') {
      agent {
        dockerfile {
          filename 'src/tests/python-36.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh 3.6'
          dir("tempsrc") { deleteDir() }
        }
        junit '3.6-results.xml'
      }
    }
    stage('Test Python RC') {
      agent {
        dockerfile {
          filename 'src/tests/python-rc.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh rc'
          dir("tempsrc") { deleteDir() }
        }
        junit 'rc-results.xml'
      }
    }
    stage('Test Ubuntu 16.04') {
      agent {
        dockerfile {
          filename 'src/tests/ubuntu.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh ubuntu'
          dir("tempsrc") { deleteDir() }
        }
        junit 'ubuntu-results.xml'
      }
    }
  }
  environment {
    PYTHONDONTWRITEBYTECODE = '1'
  }
  post {
    failure {
      mail(to: 'alr48@cl.cam.ac.uk', subject: "Failed Pipeline: ${currentBuild.fullDisplayName}", body: "Something is wrong with ${env.BUILD_URL}")
    }
  }
  options {
    checkoutToSubdirectory('src')
    timestamps()
  }
}
