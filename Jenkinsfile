pipeline {
  agent {
    node {
      label 'docker'
    }
  }
  stages {
  stage('Test') {
  parallel {
    stage('Test Python 3.6.0') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'python-360.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh 3.6.0'
          dir("tempsrc") { deleteDir() }
        }
        junit '3.6.0-results.xml'
      }
    }
    stage('Test Python 3.7.0') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'python-370.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh 3.7.0'
          dir("tempsrc") { deleteDir() }
        }
        junit '3.7.0-results.xml'
      }
    }
    stage('Test Python 3.8.0') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'python-380.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh 3.8.0'
          dir("tempsrc") { deleteDir() }
        }
        junit '3.8.0-results.xml'
      }
    }
    stage('Test Python 3.9.0') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'python-390.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh 3.9.0'
          dir("tempsrc") { deleteDir() }
        }
        junit '3.9.0-results.xml'
      }
    }
    stage('Test Python RC') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'python-rc.Dockerfile'
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
    stage('Test Ubuntu 18.04') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'ubuntu.Dockerfile'
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
